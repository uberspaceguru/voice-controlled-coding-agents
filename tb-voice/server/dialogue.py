"""Small, explicit conversation policy. No I/O, generated commands, or hidden retries."""

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from exact_values import exact_request

PENDING_TTL = 45.0
REFERENCE_TTL = 90.0
READ_THRESHOLD = 0.45
ACTION_THRESHOLD = 0.88
ACTION_ADDRESSED_THRESHOLD = 0.70
DIRECT_EXECUTE_THRESHOLD = 0.80
TARGET_THRESHOLD = 0.80


def probability(answers: dict, question: str, choice: str | None = None) -> float:
    answer = answers.get(question) or {}
    value = answer.get("noul", 0) if choice is None else (answer.get("probabilities") or {}).get(choice, 0)
    return float(value) if isinstance(value, (float, int)) and math.isfinite(value) and 0 <= value <= 1 else 0.0


def chosen(answers: dict, question: str, default: str = "unknown") -> str:
    answer = answers.get(question) or {}
    return answer.get("choice") or default


@dataclass
class Pending:
    id: int
    text: str
    target: str | None
    created: float
    stage: str | None
    kind: str = "send"
    status: str = "confirm"
    offered: bool = False


@dataclass
class Record:
    text: str
    target: str
    created: float
    status: str = "recorded"
    id: int = 0


@dataclass
class Decision:
    op: str
    reason: str
    response: str = "silent"
    text: str = ""
    target: str | None = None
    route: str = ""
    pending_id: int | None = None
    epoch: int = 0
    stage: str | None = None
    recorded_text: str = ""
    source: str = ""


class Dialogue:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.epoch = 0
        self.stage: str | None = None
        self.targets: dict[str, dict] = {}
        self.pending: Pending | None = None
        self.proposal: Record | None = None
        self.last_action: Record | None = None
        self.last_command: Record | None = None
        self.last_information: str | None = None
        self.listening = "active"
        self.recent: list[dict] = []
        self._sequence = 0
        self._seen: list[str] = []
        self._consumed: set[int] = set()
        self._committed_epochs: set[int] = set()
        self._clarification_key: tuple | None = None

    def begin(self, key: str | None = None) -> int | None:
        if not self.accept(key):
            return None
        self.epoch += 1
        self.expire()
        return self.epoch

    def accept(self, key: str | None = None) -> bool:
        if key is not None and key in self._seen:
            return False
        if key is not None:
            self._seen = (self._seen + [key])[-64:]
        return True

    def expire(self):
        if self.pending and (self.clock() - self.pending.created > PENDING_TTL or self.pending.stage != self.stage):
            self.pending = None
        for name in ("proposal", "last_command"):
            record = getattr(self, name)
            if record and self.clock() - record.created > REFERENCE_TTL:
                setattr(self, name, None)

    def sync(self, stage: str | None, targets: list[dict]):
        if self.stage != stage:
            self.pending = None
            self.proposal = None
            self.last_command = None
            self.last_information = None
            self.stage = stage
        self.targets = {t["sessionId"]: t for t in targets if isinstance(t.get("sessionId"), str)}
        if self.pending and self.pending.target and self.pending.target not in self.targets:
            self.pending = None
        for name in ("proposal", "last_command"):
            record = getattr(self, name)
            if record and record.target not in self.targets:
                setattr(self, name, None)
        self.expire()

    def snapshot(self) -> dict:
        self.expire()
        def record(value):
            if value is None:
                return None
            out = asdict(value)
            out["age"] = round(self.clock() - out.pop("created"), 2)
            return out
        return {
            "stage": self.stage, "targets": list(self.targets.values()),
            "pending": record(self.pending), "proposal": record(self.proposal),
            "last_action": record(self.last_action) if self.last_action and self.clock() - self.last_action.created <= REFERENCE_TTL else None, "last_command": record(self.last_command),
            "last_information": self.last_information, "listening": self.listening,
            "recent": self.recent[-6:],
        }

    def remember(self, text: str, act: str, decision: Decision):
        self.recent = (self.recent + [{"text": text, "act": act, "outcome": decision.reason}])[-6:]

    def offer_proposal(self, text: str, target: str):
        if text.strip() and target in self.targets:
            self.proposal = Record(text.strip(), target, self.clock())

    def command(self, text: str, target: str):
        self.last_command = Record(text, target, self.clock())

    def prepare(self, text: str, target: str | None, kind="send", status="confirm") -> Decision:
        self._sequence += 1
        if target not in self.targets:
            target = None
        if target is None:
            status = "target"
        self.pending = Pending(self._sequence, text, target, self.clock(), self.stage, kind, status)
        if target is None:
            return Decision("clarify", "target_required", "clarification", "Which agent should receive it?",
                            pending_id=self._sequence, epoch=self.epoch)
        name = self.targets[target].get("name") or self.targets[target].get("project") or "that agent"
        if kind == "start_agent":
            return Decision("clarify", "confirm_launch_directory", "clarification",
                            f"Start a new agent in {text}? Confirm or cancel.",
                            target, pending_id=self._sequence, epoch=self.epoch)
        # Read back source words; never use an answer model to invent the payload.
        return Decision("clarify", "confirm_source", "clarification",
                        f"Send to {name}: {text} Confirm or cancel.",
                        target, pending_id=self._sequence, epoch=self.epoch)

    def mark_offered(self, pending_id: int | None):
        if self.pending and self.pending.id == pending_id:
            self.pending.offered = True

    def valid(self, epoch: int, stage: str | None) -> bool:
        return self.epoch == epoch and self.stage == stage

    def _target(self, answers: dict, threshold=TARGET_THRESHOLD) -> str | None:
        selection = chosen(answers, "target", "none")
        if selection == "stage":
            selection = self.stage
        elif selection == "previous":
            record = self.last_action
            selection = record.target if record and self.clock() - record.created <= REFERENCE_TTL else None
        if selection not in self.targets:
            return None
        # Several choice keys can denote the same concrete session. Combine
        # only those known equivalents, not probabilities for other agents.
        mass = probability(answers, "target", selection)
        if selection == self.stage:
            mass += probability(answers, "target", "stage")
        previous = self.last_action
        if previous and self.clock() - previous.created <= REFERENCE_TTL and previous.target == selection:
            mass += probability(answers, "target", "previous")
        return selection if mass >= threshold else None

    def _receipt(self, text: str, reason: str) -> Decision:
        return Decision("say", reason, "receipt", text, epoch=self.epoch)

    def decide(self, text: str, answers: dict, epoch: int) -> Decision:
        decision = self._decide(text, answers, epoch)
        if decision.op == "clarify":
            key = (decision.reason, decision.target, self.pending.text if self.pending else decision.text)
            if key == self._clarification_key:
                self.pending = None
                decision = self._receipt("I couldn't resolve that request. Nothing was sent; please state it again.", "clarification_exhausted")
                self._clarification_key = None
            else:
                self._clarification_key = key
        elif decision.op != "silent":
            self._clarification_key = None
        decision.stage = self.stage
        decision.source = chosen(answers, "source")
        return decision

    def _decide(self, text: str, answers: dict, epoch: int) -> Decision:
        if epoch != self.epoch:
            return Decision("silent", "stale_judgment", epoch=epoch)
        self.expire()
        act = chosen(answers, "act")
        route = chosen(answers, "route", "custom")
        response = chosen(answers, "response", "summary")
        source = chosen(answers, "source")
        addressed = probability(answers, "addressed")
        act_p = probability(answers, "act", act)
        target = self._target(answers, 0.55 if act == "inform" else TARGET_THRESHOLD)
        execute = probability(answers, "execute")
        if act in {"ack", "think"}:
            return Decision("silent", act, epoch=epoch)
        exact = exact_request(text)
        if exact:
            act, act_p, response = "inform", 1.0, "exact_" + exact
            target = self._target(answers, 0.55)
        elif (act != "correct" and not response.startswith("exact_")
              and execute <= 0.1 and route in {"rung_goal", "rung_findings", "rung_solution", "rung_why"}
              and probability(answers, "route", route) >= 0.75):
            # Independent evidence can agree on a read while act mass is split
            # between an imperative question and a correction. This grants no work.
            act, act_p = "inform", probability(answers, "route", route)
            response = "detail" if route == "rung_why" else "summary"
            target = self._target(answers, 0.55)
        if addressed < READ_THRESHOLD or act_p < READ_THRESHOLD:
            return Decision("silent", "not_addressed_or_uncertain", epoch=epoch)
        if self.listening == "paused" and route not in {"resume_listening", "stop_speaking", "cancel"} and act != "cancel":
            return Decision("silent", "listening_paused", epoch=epoch)

        if act in {"direct", "inform", "control"} and route in {"invite_next", "teach", "speak", "summarize_recent"}:
            if probability(answers, "route", route) >= 0.75:
                self.pending = None
                return Decision("answer", "manager_information", response, text, target, route, epoch=epoch)
            if act == "inform":
                route = "custom"

        if act == "control":
            if route == "stop_speaking":
                return Decision("mute", "stop_speaking", epoch=epoch)
            if route == "pause_listening":
                self.listening = "paused"
                if self.pending:
                    self.pending.status, self.pending.offered = "held", False
                return self._receipt("Requests paused. Say resume listening.", "listening_paused")
            if route == "resume_listening":
                self.listening = "active"
                return self._receipt("Listening again. Pending work has not been sent.", "listening_resumed")
            if route == "hold":
                if self.pending:
                    self.pending.status, self.pending.offered = "held", False
                    return self._receipt("Holding the unsent request.", "held")
                return self._receipt("There is no unsent request to hold.", "nothing_to_hold")
            if route == "resume":
                if self.pending:
                    p = self.pending
                    return self.prepare(p.text, p.target, p.kind)
                return self._receipt("There is no fresh held request.", "nothing_to_resume")
            if route == "stop_agent":
                if not target:
                    return Decision("clarify", "stop_target_required", "clarification",
                                    "Which agent should receive a stop request?", epoch=epoch)
                if addressed >= ACTION_ADDRESSED_THRESHOLD and act_p >= ACTION_THRESHOLD and execute >= ACTION_THRESHOLD:
                    return Decision("dispatch", "explicit_stop_request", "receipt", text, target,
                                    "stop_agent", epoch=epoch)
                return self.prepare("Stop the current task.", target, "stop_agent")

        if act in {"cancel", "reject"}:
            self.proposal = None
            if self.pending:
                self.pending = None
                return self._receipt("Canceled the unsent request.", "pending_canceled")
            if (self.last_action and self.clock() - self.last_action.created <= REFERENCE_TTL
                    and self.last_action.status in {"sent", "dispatching", "unknown", "waiting"}):
                return self._receipt("That request already left the manager. I cannot undo it. Ask the agent to stop.", "cannot_undo")
            return self._receipt("There is no unsent request.", "nothing_to_cancel")

        if act == "confirm":
            pending = self.pending
            if (not pending and source in {"proposal", "last_command"}
                    and probability(answers, "source", source) >= TARGET_THRESHOLD):
                record = getattr(self, source)
                if record:
                    return self.prepare(record.text, target or record.target)
            if (not pending or not pending.offered or pending.status != "confirm"
                    or not pending.target or pending.stage != self.stage):
                return self._receipt("There is no fresh proposal to confirm.", "no_confirmable_proposal")
            acceptance = act_p + probability(answers, "act", "direct")
            if addressed < ACTION_ADDRESSED_THRESHOLD or acceptance < ACTION_THRESHOLD or execute < ACTION_THRESHOLD:
                return Decision("clarify", "confirmation_uncertain", "clarification",
                                "Should I send the pending request?", pending_id=pending.id, epoch=epoch)
            if (source != "pending" or probability(answers, "source", "pending") < TARGET_THRESHOLD
                    or chosen(answers, "target") == "ambiguous"
                    or (chosen(answers, "target") != "none" and target is None)
                    or (target is not None and target != pending.target)):
                return Decision("clarify", "confirmation_reference_mismatch", "clarification",
                                "Are you confirming the pending request as stated?",
                                pending_id=pending.id, epoch=epoch)
            return Decision("dispatch", "confirmed", "receipt", pending.text, pending.target,
                            pending.kind, pending.id, epoch)

        if act == "correct":
            if (self.pending and target and target != self.pending.target and execute < 0.2
                    and not response.startswith("exact_")):
                return self.prepare(self.pending.text, target, self.pending.kind)
            if (self.pending and target is None and act_p < 0.7
                    and probability(answers, "act", "reject") >= 0.25 and route == "none"):
                self.pending = None
                self.proposal = None
                return self._receipt("Canceled the unsent request.", "uncertain_repair_withdrawn")
            # Explicitly read-only repair cancels unsent execution before reading.
            if response.startswith("exact_") or (execute < 0.2 and response in {"summary", "detail"} and source != "pending"):
                unsent = self.pending
                self.pending = None
                if (not self.last_information and not unsent and self.last_action
                        and self.clock() - self.last_action.created <= REFERENCE_TTL
                        and self.last_action.status in {"sent", "dispatching", "unknown", "waiting"}):
                    return self._receipt("The earlier request already left the manager; I cannot undo it. Ask again for the information.", "cannot_undo")
                return Decision("answer", "read_only_repair", response, text,
                                target or (unsent.target if unsent else self.stage), route, epoch=epoch,
                                recorded_text=unsent.text if unsent and response == "exact_command" else "")
            if not self.pending:
                if (self.last_action and self.clock() - self.last_action.created <= REFERENCE_TTL
                    and self.last_action.status in {"sent", "dispatching", "unknown", "waiting"}):
                    return self._receipt("That request already left the manager. I cannot move or undo it; nothing was resent.", "cannot_retarget_sent")
                return Decision("clarify", "nothing_to_repair", "clarification",
                                "Which request do you want to change?", epoch=epoch)
            if source == "pending":
                return self.prepare(self.pending.text, target, self.pending.kind)
            if source == "utterance":
                # Preserve correction verbatim alongside its bound request; no guessed replacement.
                corrected = self.pending.text + "\nCorrection before execution: " + text
                return self.prepare(corrected, target or self.pending.target, self.pending.kind)
            return Decision("clarify", "repair_unclear", "clarification",
                            "Should I change the target or the instruction?", epoch=epoch)

        if act == "direct":
            self.last_information = None
            if route == "start_agent":
                directory = (self.targets.get(target) or {}).get("cwd")
                if not isinstance(directory, str) or not directory.startswith("/"):
                    return Decision("clarify", "launch_directory_required", "clarification",
                                    "Which existing agent's directory should the new agent use?", epoch=epoch)
                return self.prepare(directory, target, "start_agent")
            if probability(answers, "source", source) < TARGET_THRESHOLD:
                if source == "utterance" and probability(answers, "source", source) >= READ_THRESHOLD and not target:
                    return self.prepare(text, None, status="target")
                return Decision("clarify", "source_uncertain", "clarification",
                                "What exact instruction should I send?", epoch=epoch)
            if self.pending and self.pending.status == "target" and source == "pending":
                return self.prepare(self.pending.text, target, self.pending.kind)
            if source in {"proposal", "last_command", "pending"}:
                record = self.pending if source == "pending" else getattr(self, source)
                if record is None:
                    return Decision("clarify", "reference_missing", "clarification",
                                    "Which exact instruction do you mean?", epoch=epoch)
                selected = chosen(answers, "target", "none")
                resolved = target
                if resolved is None and selected in {"none", "stage", "previous"}:
                    resolved = record.target  # named explicitly in a new confirmation, never dispatched yet
                return self.prepare(record.text, resolved)
            if source != "utterance":
                return Decision("clarify", "reference_ambiguous", "clarification",
                                "What should I send, and to which agent?", epoch=epoch)
            if not target:
                return self.prepare(text, None, status="target")
            if (addressed < ACTION_ADDRESSED_THRESHOLD or act_p < ACTION_THRESHOLD
                    or execute < DIRECT_EXECUTE_THRESHOLD):
                return self.prepare(text, target)
            self.pending = None
            return Decision("dispatch", "explicit_instruction", "receipt", text, target, "send", epoch=epoch)

        if act == "inform":
            # Resolving a named target alone can complete a target clarification.
            if self.pending and self.pending.status == "target" and source == "pending" and target:
                return self.prepare(self.pending.text, target, self.pending.kind)
            self.pending = None
            if (source in {"last_command", "last_action"}
                    and probability(answers, "source", source) >= TARGET_THRESHOLD
                    and chosen(answers, "target") in {"previous", "stage", "none"}):
                record = getattr(self, source)
                if record and record.target in self.targets and self.clock() - record.created <= REFERENCE_TTL:
                    target = record.target
            if chosen(answers, "target") not in {"none"} and target is None:
                return Decision("clarify", "information_target_ambiguous", "clarification",
                                "Which agent are you asking about?", epoch=epoch)
            if source == "unknown":
                return Decision("clarify", "information_source_missing", "clarification",
                                "Which recorded fact are you asking for?", epoch=epoch)
            exact = exact_request(text)
            if exact:
                response = "exact_" + exact
            self.last_information = response
            if source == "last_command":
                record = self.last_command
                if not record or (target and target != record.target):
                    return Decision("clarify", "command_reference_missing", "clarification",
                                    "Which command do you mean?", epoch=epoch)
                return Decision("answer", "recorded_command", response, text, record.target, route,
                                epoch=epoch, recorded_text=record.text)
            if source == "last_action":
                record = self.last_action
                if (not record or record.status != "sent"
                        or self.clock() - record.created > REFERENCE_TTL
                        or (target and target != record.target)):
                    return self._receipt("I don't have a fresh confirmed sent request to read.", "sent_record_missing")
                return Decision("answer", "last_sent_request", response, text, record.target, route,
                                epoch=epoch, recorded_text=record.text)
            return Decision("answer", "information", response, text, target or self.stage, route, epoch=epoch)

        return Decision("clarify", "meaning_unclear", "clarification",
                        "Are you asking for information or asking me to send work?", epoch=epoch)

    def commit(self, decision: Decision) -> Record | None:
        """The irreversible boundary. Called only after fresh live-target revalidation."""
        if (decision.op != "dispatch" or decision.stage != self.stage
                or decision.epoch != self.epoch or decision.target not in self.targets
                or decision.epoch in self._committed_epochs):
            return None
        if decision.pending_id:
            p = self.pending
            if (not p or p.id != decision.pending_id or not p.offered or p.status != "confirm"
                    or p.stage != self.stage):
                return None
            if p.id in self._consumed or self.clock() - p.created > PENDING_TTL:
                return None
            self._consumed.add(p.id)
        self.pending = None
        self.proposal = None
        self._committed_epochs.add(decision.epoch)
        self._sequence += 1
        action = Record(decision.text, decision.target, self.clock(), "dispatching", self._sequence)
        self.last_action = action
        return action
