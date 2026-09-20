"""Synthetic dialogue fixtures shared by unit and live routing evaluations.

Each corpus turn supplies a complete pre-turn context. Group names preserve the
conversation narrative; runners reset from this context so one model mistake
does not contaminate later scores. Separate unit tests cover stateful sequences.
No function reads runtime configuration, transcripts, credentials, or the fleet.
"""

from dialogue import Decision, Dialogue, Pending, Record


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def choice(value, probability=0.98):
    """A full mocked distribution: confidence alone must not permit execution."""
    return {
        "choice": value,
        "confidence": 1.0,
        "probabilities": {value: probability, "__other__": 1.0 - probability},
    }


def semantic_answers(
    act="direct", target="stage", source="utterance", response="receipt",
    route="send_message", addressed=0.98, execute=0.98,
):
    return {
        "act": choice(act), "target": choice(target), "source": choice(source),
        "response": choice(response), "route": choice(route),
        "addressed": {"noul": addressed}, "execute": {"noul": execute},
    }


def mocked_judgment(turn):
    expected = turn["expect"]
    return semantic_answers(
        act=expected["act"], target=expected["target"], source=expected["source"],
        response=expected["response"], route=expected.get("route", "custom"),
        addressed=0.98 if expected["addressed"] else 0.02,
        execute=0.98 if expected["execution_now"] else 0.02,
    )


def seed_dialogue(turn, fixtures):
    """Build only synthetic state. Call begin() before snapshot()/decide()."""
    clock = Clock(float(fixtures.get("clock", {}).get("now", 1000)))
    dialogue = Dialogue(clock)
    ctx = turn["context"]
    targets = [t.copy() for t in fixtures["targets"] if t["sessionId"] in ctx["candidates"]]
    dialogue.sync(ctx["stage"], targets)
    dialogue.listening = ctx["listening"]
    dialogue._sequence = 100
    for field in ("proposal", "last_action", "last_command"):
        spec = ctx.get(field)
        if spec:
            setattr(dialogue, field, Record(
                spec["text"], spec["target"], clock() - spec.get("age_seconds", 0),
                spec.get("status", "recorded"), 1,
            ))
    spec = ctx.get("pending")
    if spec:
        status = {"offered": "confirm", "needs_target": "target", "held": "held"}[spec["status"]]
        dialogue.pending = Pending(
            1, spec["text"], spec["target"], clock() - spec.get("age_seconds", 0),
            spec.get("stage_at_offer"), spec.get("kind", "send"), status,
            spec["status"] == "offered",
        )
    if ctx.get("previous_turn"):
        dialogue.last_information = ctx["previous_turn"].get("response")
        previous = ctx["previous_turn"]
        dialogue.recent = [{
            "text": previous.get("text", ""), "act": previous.get("act", "inform"),
            "outcome": previous.get("response", "information"),
        }]
    return dialogue


def begin_turn(dialogue, turn):
    key = turn.get("transcript", {}).get("id", turn["id"])
    if turn.get("transcript", {}).get("same_as_previous"):
        dialogue.begin(key)
    return dialogue.begin(key)


def policy_observation(dialogue, turn, judgment, epoch):
    """Run policy and simulated boundary events; never dispatch or produce audio.

    Races model state invalidation only. Actual task cancellation, delayed model
    responses, native interruption and spoken delivery need integration tests.
    Delivery status comes from the fixture, not a real CLI invocation.
    """
    stage = dialogue.stage
    if epoch is None:
        decision = Decision("silent", "duplicate_transcript")
    else:
        for event in turn.get("events", []):
            if event["at"] == "judgment_inflight" and event["type"] == "cancel":
                cancel_epoch = dialogue.begin()
                dialogue.decide(event["text"], semantic_answers(act="cancel"), cancel_epoch)
        decision = dialogue.decide(turn["text"], judgment, epoch)
        for event in turn.get("events", []):
            if event["at"] == "answer_inflight":
                if event["type"] == "stage_changed":
                    dialogue.sync(event["stage"], list(dialogue.targets.values()))
                elif event["type"] == "cancel":
                    cancel_epoch = dialogue.begin()
                    dialogue.decide(event["text"], semantic_answers(act="cancel"), cancel_epoch)
        if not dialogue.valid(epoch, stage):
            decision = Decision("silent", "superseded", epoch=epoch)
    action = dialogue.commit(decision) if decision.op == "dispatch" else None
    if action:
        code = turn.get("delivery", {}).get("exit_code", 0)
        action.status = {0: "sent", 2: "not_dispatched", 3: "waiting", 4: "ambiguous", 5: "failed"}.get(code, "unknown")
    return {
        "op": decision.op, "reason": decision.reason, "response": decision.response, "route": decision.route,
        "text": decision.text, "target": decision.target,
        "recorded_text": decision.recorded_text,
        "dispatch_count": int(action is not None),
        "dispatch_target": action.target if action else None,
        "dispatch_text": action.text if action else None,
        "delivery_status": action.status if action else None,
        "pending_status": dialogue.pending.status if dialogue.pending else None,
        "pending_target": dialogue.pending.target if dialogue.pending else None,
        "listening": dialogue.listening,
        "delivery_is_simulated": bool(action),
    }


def contract_errors(turn, observed):
    """Side-effect and response-policy contracts, independent of label accuracy."""
    expected = turn["expect"]
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    if "operation" in expected:
        require(observed["op"] == expected["operation"], "operation")
    if "policy_route" in expected:
        require(observed.get("route") == expected["policy_route"], "policy_route")
    require(observed["dispatch_count"] == expected["dispatch_count"], "dispatch_count")
    if expected["dispatch_count"]:
        require(observed["dispatch_target"] == expected["dispatch_target"], "dispatch_target")
        require(observed["dispatch_text"] == expected["dispatch_text"], "dispatch_text")
        require(observed["op"] == "dispatch", "dispatch_operation")
    disposition_ops = {
        "offer_confirmation": {"clarify"}, "repair_pending": {"clarify"},
        "clarify_target": {"clarify"}, "clarify_source": {"clarify"},
        "stale_reference": {"clarify"}, "no_pending": {"say", "clarify"},
        "stale_pending": {"say", "clarify"}, "stage_changed": {"say", "clarify"},
        "already_dispatched": {"say", "clarify"}, "cancel_pending": {"say"},
        "cancel_inflight": {"say"}, "hold_pending": {"say"},
        "pause_listening": {"say"}, "resume_listening": {"say"},
        "paused": {"silent"}, "stop_speaking": {"mute"},
    }
    allowed = disposition_ops.get(expected["disposition"])
    if allowed:
        require(observed["op"] in allowed, "disposition_operation")
    if observed["op"] == "clarify":
        require(observed["response"] == "clarification", "clarification_mode")
    if observed["op"] == "say":
        require(observed["response"] == "receipt", "receipt_mode")
    if expected["response"] == "silent" or expected["disposition"] in {"superseded", "duplicate"}:
        require(observed["op"] in {"silent", "mute"}, "must_stay_silent")
    if expected["disposition"] in {"answer", "answer_exact", "cancel_and_answer"}:
        require(observed["op"] == "answer", "answer_required")
        require(observed["response"] == expected["response"], "response_mode")
        selection = expected["target"]
        ctx = turn["context"]
        target = selection
        if selection == "stage":
            target = ctx["stage"]
        elif selection == "previous":
            prior = ctx.get("last_action") or ctx.get("previous_turn") or {}
            target = prior.get("target")
        elif selection in {"none", "ambiguous"}:
            target = None
        if target:
            require(observed["target"] == target, "answer_target")
        if expected.get("literal") and expected["source"] in {"last_command", "pending"}:
            require(observed["recorded_text"] == expected["literal"], "recorded_literal")
    if expected.get("cannot_undo"):
        require("cannot" in observed["text"].lower(), "truthful_cannot_undo")
    pending_status = expected.get("pending_status")
    if pending_status:
        status = {"cleared": None, "offered": "confirm", "needs_target": "target", "held": "held"}[pending_status]
        require(observed["pending_status"] == status, "pending_status")
    if expected.get("listening_after"):
        require(observed["listening"] == expected["listening_after"], "listening_state")
    if expected["disposition"] in {"waiting", "failed"}:
        require(observed["delivery_status"] == expected["disposition"], "delivery_state")
    return errors
