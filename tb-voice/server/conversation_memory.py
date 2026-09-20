"""Bounded, session-local evidence of questions, source changes, and delivery.

This module judges no semantics. A completed generic answer remains relevance
unverified; only an exact copy of an observed scalar can resolve an exact fact.
Nothing here sends commands, speaks, persists data, or calls a provider.
"""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

FRESHNESS = 300.0
PENDING_FRESHNESS = 45.0
ACTION_FRESHNESS = 90.0
EXACT_KINDS = frozenset({"directory", "branch", "command", "identifier"})
_MAX_TEXT = 4096
_MAX_PAYLOAD_BYTES = 65536


@dataclass
class Question:
    id: int
    text: str
    target: str
    kind: str
    created: float
    status: str = "open"
    acknowledgement_at: float | None = None
    repair_count: int = 0
    superseded_by: int | None = None
    answer_text: str | None = None
    delivery_status: str | None = None
    delivery_id: str | None = None
    source_id: str | None = None
    source_observed_at: float | None = None
    resolved_at: float | None = None
    cancellation_source_id: str | None = None
    canceled_at: float | None = None


@dataclass(frozen=True)
class Observation:
    id: int
    source_id: str
    target: str
    created: float
    signature: str
    _payload_json: str

    @property
    def payload(self) -> Any:
        """A copy: callers cannot mutate the evidence after it was signed."""
        return json.loads(self._payload_json)


@dataclass(frozen=True)
class DecisionRecord:
    target: str
    status: str
    source_id: str
    text: str
    created: float


@dataclass(frozen=True)
class _Baseline:
    signature: str
    delivered_at: float
    source_id: str
    source_observed_at: float
    text: str
    delivery_id: str | None


def _text(value: str, name: str, maximum=_MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a nonempty bounded string")
    return value


def _contains_scalar(payload: Any, expected: str) -> bool:
    if isinstance(payload, str):
        return payload == expected
    if isinstance(payload, dict):
        return any(_contains_scalar(value, expected) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_scalar(value, expected) for value in payload)
    return False


def _field(record, name, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


class Memory:
    def __init__(self, clock: Callable[[], float] = time.monotonic, capacity: int = 32):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self.clock = clock
        self.capacity = capacity
        self._sequence = 0
        self._questions: OrderedDict[int, Question] = OrderedDict()
        self._observations: OrderedDict[int, Observation] = OrderedDict()
        self._baselines: OrderedDict[str, _Baseline] = OrderedDict()
        self._deliveries: OrderedDict[tuple, float] = OrderedDict()
        self._decisions: OrderedDict[str, DecisionRecord] = OrderedDict()

    def _fresh(self, created: float, limit=FRESHNESS) -> bool:
        age = self.clock() - created
        return 0 <= age <= limit

    def _trim(self, records: OrderedDict, capacity: int | None = None):
        while len(records) > (capacity or self.capacity):
            records.popitem(last=False)

    def _expire(self):
        for question in self._questions.values():
            if question.status in {"open", "relevance_unverified"} and not self._fresh(question.created):
                question.status = "expired"
        for key, observation in list(self._observations.items()):
            if not self._fresh(observation.created):
                del self._observations[key]
        for target, baseline in list(self._baselines.items()):
            if not self._fresh(baseline.delivered_at):
                del self._baselines[target]
        for key, created in list(self._deliveries.items()):
            if not self._fresh(created):
                del self._deliveries[key]
        for target, decision in list(self._decisions.items()):
            if not self._fresh(decision.created):
                del self._decisions[target]

    def _active(self, question: Question) -> bool:
        return (
            self._questions.get(question.id) is question
            and self._fresh(question.created)
            and question.status in {"open", "relevance_unverified"}
        )

    def _observed(self, observation: Observation) -> bool:
        return self._observations.get(observation.id) is observation and self._fresh(observation.created)

    def _delivery_once(self, scope: tuple, delivery_id: str | None) -> bool:
        if delivery_id is None:
            return True
        _text(delivery_id, "delivery_id", 512)
        key = (*scope, delivery_id)
        if key in self._deliveries:
            return False
        self._deliveries[key] = self.clock()
        self._trim(self._deliveries, self.capacity * 4)
        return True

    def ask(self, text: str, target: str, kind: str) -> Question:
        """A newer exact question replaces the same kind for the same agent."""
        self._expire()
        _text(text, "question")
        _text(target, "target", 512)
        _text(kind, "kind", 128)
        self._sequence += 1
        question = Question(self._sequence, text, target, kind, self.clock())
        if kind in EXACT_KINDS:
            for previous in self._questions.values():
                if previous.target == target and previous.kind == kind and self._active(previous):
                    previous.status = "superseded"
                    previous.superseded_by = question.id
        self._questions[question.id] = question
        while len(self._questions) > self.capacity:
            _, evicted = self._questions.popitem(last=False)
            if evicted.status in {"open", "relevance_unverified"}:
                evicted.status = "evicted"
        return question

    def observe(self, source_id: str, target: str, payload: Any) -> Observation:
        self._expire()
        _text(source_id, "source_id", 512)
        _text(target, "target", 512)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        raw = encoded.encode("utf-8")
        if len(raw) > _MAX_PAYLOAD_BYTES:
            raise ValueError("source payload exceeds the bounded observation size")
        self._sequence += 1
        observation = Observation(
            self._sequence, source_id, target, self.clock(), hashlib.sha256(raw).hexdigest(), encoded,
        )
        self._observations[observation.id] = observation
        self._trim(self._observations)
        return observation

    def acknowledged(self, question: Question) -> bool:
        self._expire()
        if (self._questions.get(question.id) is not question or not self._fresh(question.created)
                or question.status not in {"open", "relevance_unverified", "resolved"}):
            return False
        question.acknowledgement_at = self.clock()
        return True

    def remember_decision(self, target: str, status: str, source_id: str, text: str = "") -> DecisionRecord:
        """Record an actual policy transition; this method never performs one.

        Only the latest explicit decision for each target is kept. Proposed is
        evidence of a proposal, not authorization or delivery. Actual dispatch
        remains the caller's independently recorded last_action.
        """
        self._expire()
        _text(target, "target", 512)
        _text(source_id, "source_id", 512)
        if status not in {"proposed", "canceled", "superseded", "held"}:
            raise ValueError("decision status must describe a supported explicit policy transition")
        if not isinstance(text, str) or len(text) > _MAX_TEXT:
            raise ValueError("decision text must be a bounded string")
        decision = DecisionRecord(target, status, source_id, text, self.clock())
        self._decisions[target] = decision
        self._decisions.move_to_end(target)
        self._trim(self._decisions)
        return decision

    def claim_repair(self, question: Question) -> bool:
        self._expire()
        if not self._active(question) or question.repair_count:
            return False
        question.repair_count = 1
        return True

    def answer(
        self, question: Question, observation: Observation, text: str,
        delivery_status: str, expected: str | None = None, delivery_id: str | None = None,
    ) -> bool:
        """Return True only for resolved exact facts, never semantic relevance.

        The caller must observe the copied exact value as a scalar in its source
        payload. A substring in prose, a key name, or an invented expected value
        is insufficient. Acknowledgment, generation, and interruption never close
        a question; an eventual output_complete may reuse the same delivery ID.
        """
        self._expire()
        if not self._active(question) or not self._observed(observation) or question.target != observation.target:
            return False
        if not isinstance(text, str) or len(text) > _MAX_TEXT:
            return False
        question.answer_text = text
        question.delivery_status = delivery_status
        question.delivery_id = delivery_id
        question.source_id = observation.source_id
        question.source_observed_at = observation.created
        if delivery_status != "output_complete":
            return False
        exact = question.kind in EXACT_KINDS
        matches = (
            isinstance(expected, str) and bool(expected.strip()) and text == expected
            and _contains_scalar(observation.payload, expected)
        )
        if exact and not matches:
            return False
        if not self._delivery_once(("answer", question.id), delivery_id):
            return False
        if not exact:
            question.status = "relevance_unverified"
            return False
        question.status = "resolved"
        question.resolved_at = self.clock()
        return True

    def should_update(self, observation: Observation, critical: bool = False) -> bool:
        self._expire()
        if not self._observed(observation):
            return False
        if critical:
            return True
        baseline = self._baselines.get(observation.target)
        return baseline is None or baseline.signature != observation.signature

    def delivered_update(
        self, observation: Observation, text: str, delivery_status: str, delivery_id: str | None = None,
    ) -> bool:
        """Advance the comparison baseline only after observed output completion."""
        self._expire()
        if not self._observed(observation) or delivery_status != "output_complete":
            return False
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT:
            return False
        if not self._delivery_once(("update", observation.target), delivery_id):
            return False
        self._baselines[observation.target] = _Baseline(
            observation.signature, self.clock(), observation.source_id, observation.created, text, delivery_id,
        )
        self._baselines.move_to_end(observation.target)
        self._trim(self._baselines)
        return True

    def unresolved(self, target: str) -> Question | None:
        self._expire()
        return next((q for q in reversed(self._questions.values()) if q.target == target and self._active(q)), None)

    def cancel_question(self, target: str, source_id: str) -> Question | None:
        """Cancel only the latest fresh unanswered question for this target."""
        _text(target, "target", 512)
        _text(source_id, "source_id", 512)
        question = self.unresolved(target)
        if question is None:
            return None
        question.status = "canceled"
        question.cancellation_source_id = source_id
        question.canceled_at = self.clock()
        return question

    def resume(self, target: str, label: str, pending=None, last_action=None) -> str:
        """A source-bound receipt, not a generated summary or a new instruction."""
        self._expire()
        label = " ".join(_text(label, "label", 200).split())
        parts = [f"Back with {label}."]
        question = next((q for q in reversed(self._questions.values())
                         if q.target == target and q.kind in EXACT_KINDS and self._active(q)), None)
        if question:
            name = {"identifier": "session identifier"}.get(question.kind, question.kind)
            parts.append(f"Your exact {name} question is still unanswered.")
        elif self.unresolved(target):
            parts.append("A previous question still needs a verified answer.")
        remembered = self._decisions.get(target)
        if remembered:
            sentence = {
                "canceled": "The last explicit decision canceled an unsent request.",
                "superseded": "The last explicit decision superseded an earlier request.",
                "held": "The last explicit decision put an unsent request on hold.",
            }.get(remembered.status)
            if sentence:
                parts.append(sentence)
        if self._resume_record(pending, target, PENDING_FRESHNESS):
            status = _field(pending, "status")
            if status == "held":
                parts.append("An unsent request is on hold.")
            elif status in {"confirm", "offered", "awaiting_confirmation"}:
                parts.append("An unsent request is waiting for confirmation.")
        if self._resume_record(last_action, target, ACTION_FRESHNESS):
            sentence = {
                "sent": "The last request was sent; completion is not confirmed.",
                "waiting": "The last request is waiting for delivery.",
                "deferred": "The last request is waiting for delivery.",
                "dispatching": "Delivery of the last request is in progress.",
                "failed": "The last request failed to send.",
                "not_sent": "The last request was not sent.",
                "not_dispatched": "The last request was not sent.",
                "unknown": "Delivery of the last request is unconfirmed.",
                "ambiguous": "Delivery of the last request is unconfirmed.",
            }.get(_field(last_action, "status"))
            if sentence:
                parts.append(sentence)
        return " ".join(parts)

    def _resume_record(self, record, target, limit):
        if record is None or _field(record, "target") != target:
            return False
        created = _field(record, "created")
        return isinstance(created, (float, int)) and self._fresh(created, limit)
