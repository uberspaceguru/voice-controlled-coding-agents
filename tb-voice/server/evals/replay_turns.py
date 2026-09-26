"""Replay a labelled session and count the split turns the new rules would join.

Input: the session audit's per-utterance JSONL (one object per utterance, with
`ts` = the moment the turn was committed, `utterance_s` = commit minus the
moment "hearing" began, `labels`, `voice_start`, `dead_air_s`,
`model_latency_ms`, `voice_gate`, `reached_director`, `who`, `via`, `text`).
Nothing is sent anywhere; this reads one file and prints counts.

For every labelled FRAGMENT it finds the utterance it was split from (a
neighbouring line of his, closest first, another fragment preferred) and
asks, in order:

1. held: the first part ends holding the floor (turn_end.holds_floor) and his
   next words began within HOLD_SECS of where the first part's speech ended,
   so the end-of-turn rule keeps both in one turn. Speech end is estimated as
   commit minus OLD_EOT_DELAY (the old 1.2 s silence rule, the latest it could
   have been), and the next start is "hearing", which lags his real onset:
   both errors make the pause look longer, so this undercounts.
2. merged: the second part was committed while the first part's ask was still
   pending (reached Director and its reply had not started): it joins that
   turn. Reply start is the logged first audible word; with no audible reply,
   the ask is taken as pending for its logged model latency (else the session
   median).
3. call: the first part is a bare "Hey, Director" call, which the existing
   call window already joins to the next line (not a change here).
A second part that is itself a bare call is never merged (as in the manager).
Otherwise the fragment stays split, with the reason.

The forecast rule cannot be replayed (the session kept no forecast), so the
counts are a floor for rule 1.
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from turn_end import HOLD_SECS, holds_floor  # noqa: E402

OLD_EOT_DELAY = 1.2
PAIR_BONUS = 10.0


def _clock(row, field):
    """A local HH:MM:SS.mmm field as an absolute time, on the row's own day."""
    value = row.get(field)
    if not value:
        return None
    day = datetime.fromtimestamp(row["ts"])
    t = datetime.strptime(value, "%H:%M:%S.%f")
    return datetime(day.year, day.month, day.day, t.hour, t.minute, t.second, t.microsecond).timestamp()


def his_lines(rows):
    return [r for r in rows if r.get("in_scope", True) and r.get("who") == "AHMED" and r.get("via") == "voice"]


def pairs(lines):
    """(first, second) utterance pairs, one per split, covering every FRAGMENT.
    Neighbouring lines are paired closest first, measured from the first's
    commit to the second's start, with two fragments counted PAIR_BONUS
    seconds closer than a fragment and a whole line."""
    frag = [("FRAGMENT" in (r.get("labels") or [])) for r in lines]

    def gap(i):
        return abs(lines[i + 1]["ts"] - (lines[i + 1].get("utterance_s") or 0.0) - lines[i]["ts"])
    candidates = sorted((gap(i) - PAIR_BONUS * (frag[i] and frag[i + 1]), i)
                        for i in range(len(lines) - 1) if frag[i] or frag[i + 1])
    used, out = set(), []
    for _, i in candidates:
        if i in used or i + 1 in used:
            continue
        used.update((i, i + 1))
        out.append((lines[i], lines[i + 1]))
    return sorted(out, key=lambda p: p[0]["ts"])


def classify(first, second, latency_default):
    start_second = second["ts"] - (second.get("utterance_s") or 0.0)
    pause = start_second - (first["ts"] - OLD_EOT_DELAY)
    hold = holds_floor(first.get("text") or "")
    if hold and pause < HOLD_SECS:
        return "held", f"first part ends {hold}; pause about {pause:.1f} s"
    if first.get("voice_intent") == "director_call":
        return "call", "bare call; the call window joins the next line"
    if second.get("voice_intent") == "director_call":
        return "split", "second part is a bare call: it opens the call window, it is not merged"
    if not first.get("reached_director"):
        return "split", "first part was ignored by the voice gate: no pending ask to join"
    reply_at = _clock(first, "voice_start") if first.get("audible") else None
    if reply_at is None:
        latency = (first.get("model_latency_ms") or latency_default) / 1000.0
        reply_at = first["ts"] + latency
        basis = "ask in flight"
    else:
        basis = "reply not yet started"
    if second["ts"] < reply_at:
        return "merged", f"{basis}; second part came {reply_at - second['ts']:.1f} s before the reply"
    return "split", f"second part came {second['ts'] - reply_at:.1f} s after the reply started"


def replay(rows):
    lines = his_lines(rows)
    latencies = [r["model_latency_ms"] for r in rows if r.get("model_latency_ms")]
    latency_default = statistics.median(latencies) if latencies else 1500.0
    fragments = sum("FRAGMENT" in (r.get("labels") or []) for r in lines)
    results = []
    for first, second in pairs(lines):
        outcome, why = classify(first, second, latency_default)
        results.append({"first": first["time"], "outcome": outcome, "why": why,
                         "fragments": sum("FRAGMENT" in (r.get("labels") or []) for r in (first, second)),
                         "texts": [first.get("text"), second.get("text")]})
    summary = {"fragment_rows": fragments, "splits": len(results)}
    for key in ("held", "merged", "call", "split"):
        summary[key + "_splits"] = sum(r["outcome"] == key for r in results)
        summary[key + "_fragments"] = sum(r["fragments"] for r in results if r["outcome"] == key)
    return summary, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jsonl", help="the session's per-utterance JSONL")
    parser.add_argument("--details", action="store_true", help="one line per split")
    args = parser.parse_args(argv)
    with open(os.path.expanduser(args.jsonl)) as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    summary, results = replay(rows)
    if args.details:
        for r in results:
            print(f"{r['first']}  {r['outcome']:<6}  {r['why']}  | {r['texts'][0]!r} / {r['texts'][1]!r}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
