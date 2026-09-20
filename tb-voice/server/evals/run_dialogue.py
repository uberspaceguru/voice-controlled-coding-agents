"""Bounded live semantic evaluation against synthetic conversation snapshots.

This invokes only the judgment provider. Dialogue.decide/commit run locally with
synthetic fixtures; no agent, native app, speech service, or real transcript is
accessed. Per-turn states are seeded independently, not a live audio conversation.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dialogue import (
    ACTION_ADDRESSED_THRESHOLD,
    ACTION_THRESHOLD,
    DIRECT_EXECUTE_THRESHOLD,
    READ_THRESHOLD,
    TARGET_THRESHOLD,
    chosen,
    probability,
)
from dialogue_questions import build_questions, judgment_state
from evals.dialogue_fixture import begin_turn, contract_errors, policy_observation, seed_dialogue
from manager import INTENTS, JEV_URL, JevClient


def latency_summary(values: list[float]) -> dict:
    """Milliseconds; p95 is nearest-rank, including for small sample counts."""
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 2) if ordered else None,
        "p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 2) if ordered else None,
        "p95_method": "nearest_rank",
    }


def semantic_observation(answers: dict, expected: dict) -> dict:
    """Labels are diagnostic measurements, separate from executable policy."""
    actual = {key: chosen(answers, key) for key in ("act", "target", "source", "response", "route")}
    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in actual if key in expected and actual[key] != expected[key]
    }
    for field, question, threshold in (
        ("addressed", "addressed", READ_THRESHOLD),
        ("execution_now", "execute", ACTION_THRESHOLD),
    ):
        actual[field] = probability(answers, question) >= threshold
        if field in expected and actual[field] != expected[field]:
            mismatches[field] = {"expected": expected[field], "actual": actual[field]}
    return {
        "actual": actual,
        "strict_act_match": actual["act"] == expected["act"],
        "all_labeled_fields_match": not mismatches,
        "mismatches": mismatches,
    }


def summarize(rows: list[dict]) -> dict:
    classified = [row for row in rows if "semantic" in row]
    observed = [row for row in rows if "policy" in row]
    calls = [row["classifier_http_ms"] for row in rows if "classifier_http_ms" in row]
    successful_calls = [row["classifier_http_ms"] for row in classified]
    errors = [row for row in rows if "error" in row]
    by_case: dict[str, set[str]] = {}
    for row in observed:
        by_case.setdefault(row["case_id"], set()).add(json.dumps(row["policy"]["actual"], sort_keys=True))
    return {
        "attempted_cases": len(rows),
        "classifier_http": latency_summary(calls),
        "successful_classifier_http": latency_summary(successful_calls),
        "complete_synthetic_routing": latency_summary([row["routing_ms"] for row in rows]),
        "http_failures": len([row for row in errors if row["error"]["phase"] == "classifier_http"]),
        "local_failures": len([row for row in errors if row["error"]["phase"] != "classifier_http"]),
        "strict_act": {"n": len(classified), "matched": sum(row["semantic"]["strict_act_match"] for row in classified)},
        "all_labeled_fields": {"n": len(classified), "matched": sum(row["semantic"]["all_labeled_fields_match"] for row in classified)},
        "policy_effects": {"n": len(observed), "matched": sum(row["policy"]["passed"] for row in observed)},
        "unexpected_dispatches": sum(
            row["policy"]["actual"].get("dispatch_count", 0) > 0 and row["expected"].get("dispatch_count", 0) == 0
            for row in observed
        ),
        "policy_variation_case_ids": sorted(case for case, outcomes in by_case.items() if len(outcomes) > 1),
        "semantic_mismatch_case_ids": [row["case_id"] for row in classified if not row["semantic"]["all_labeled_fields_match"]],
        "policy_mismatch_case_ids": [row["case_id"] for row in observed if not row["policy"]["passed"]],
    }


def compare_policy(actual: dict, turn: dict) -> dict:
    errors = contract_errors(turn, actual)
    return {"actual": actual, "passed": not errors, "contract_errors": errors}


def write_report(path: Path, metadata: dict, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {**metadata, "summary": summarize(rows), "cases": rows}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


async def evaluate(args) -> dict:
    # The caller loads an existing credential securely into the environment.
    # No dotenv, local secret-store inspection, or credential values in output.
    key = os.environ.get("JEV_API_KEY")
    if not key:
        raise ValueError("JEV_API_KEY must be provided in the environment")
    corpus = json.loads((args.corpus or Path(__file__).with_name("dialogue_conversations.json")).read_text())
    selected = [
        (group, turn)
        for group in corpus["conversations"]
        if args.split == "all" or group["split"] == args.split
        for turn in group["turns"]
    ]
    if not selected:
        raise ValueError("The requested split contains no cases")
    if len(selected) > 256:
        raise ValueError("Refusing more than 256 corpus cases per run")
    metadata = {
        "schema_version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "split": args.split,
        "runs": args.runs,
        "corpus_path": str(args.corpus or Path(__file__).with_name("dialogue_conversations.json")),
        "corpus_sha256": hashlib.sha256((args.corpus or Path(__file__).with_name("dialogue_conversations.json")).read_bytes()).hexdigest(),
        "model_requested": "jev-latest",
        "source_sha256": {name: hashlib.sha256(
            Path(__file__).parents[1].joinpath(name).read_bytes()).hexdigest()
            for name in ("dialogue.py", "dialogue_questions.py", "evals/dialogue_conversations.json")},
        "endpoint": JEV_URL,
        "scope": {
            "synthetic_text_only": True,
            "per_turn_seeded_context": True,
            "native_dispatch": False,
            "audio_measured": False,
            "time_to_first_audio_ms": None,
            "routing_description": "Fixture snapshot plus live semantic HTTP call plus local dialogue decision/commit. Delivery is simulated; no handler or audio runs.",
            "timing_description": "Classifier timing surrounds the HTTP client call including serialization/parsing. Routing timing includes that call and local fixture/policy work, excluding report writes.",
        },
        "thresholds": {"read": READ_THRESHOLD, "action": ACTION_THRESHOLD, "direct_execution": DIRECT_EXECUTE_THRESHOLD,
                       "action_addressed": ACTION_ADDRESSED_THRESHOLD, "target_source": TARGET_THRESHOLD},
    }
    rows = []
    client = JevClient(key)
    try:
        with patch("manager.record"), patch("manager.EXCHANGE", []):
            for repeat in range(1, args.runs + 1):
                for group, turn in selected:
                    start = time.perf_counter()
                    row = {
                        "case_id": turn["id"], "conversation_id": group["id"],
                        "split": group["split"], "run": repeat,
                        "text": turn["text"], "expected": turn["expect"],
                    }
                    phase = "fixture"
                    try:
                        dialogue = seed_dialogue(turn, corpus["fixtures"])
                        epoch = begin_turn(dialogue, turn)
                        if epoch is None:
                            actual = policy_observation(dialogue, turn, {}, epoch)
                            row["policy"] = compare_policy(actual, turn)
                            row["skipped_classifier"] = "duplicate_transcript"
                        else:
                            state = judgment_state(turn["text"], dialogue.snapshot())
                            questions = build_questions(INTENTS, state["conversation"]["targets"])
                            row["state"] = state
                            row["questions"] = questions
                            phase = "classifier_http"
                            http_start = time.perf_counter()
                            try:
                                answers = await client.ask(state, questions)
                            finally:
                                row["classifier_http_ms"] = round((time.perf_counter() - http_start) * 1000, 3)
                            row["answers"] = answers
                            row["semantic"] = semantic_observation(answers, turn["expect"])
                            phase = "policy"
                            actual = policy_observation(dialogue, turn, answers, epoch)
                            row["policy"] = compare_policy(actual, turn)
                    except Exception as exc:
                        # Exception strings or response bodies can contain request
                        # details; retain only type/status and the failing phase.
                        response = getattr(exc, "response", None)
                        row["error"] = {
                            "phase": phase, "type": type(exc).__name__,
                            "http_status": getattr(response, "status_code", None),
                        }
                    row["routing_ms"] = round((time.perf_counter() - start) * 1000, 3)
                    rows.append(row)
                    write_report(args.output, metadata, rows)
    finally:
        await client._client.aclose()
    metadata["completed_at"] = datetime.now(UTC).isoformat()
    write_report(args.output, metadata, rows)
    return summarize(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "held_out", "all"), default="development")
    parser.add_argument("--runs", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, help="Optional separate frozen corpus")
    args = parser.parse_args()
    try:
        summary = asyncio.run(evaluate(args))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output.resolve()), "summary": summary}, indent=2))
    return int(summary["http_failures"] > 0 or summary["local_failures"] > 0 or bool(summary["policy_mismatch_case_ids"]))


if __name__ == "__main__":
    raise SystemExit(main())
