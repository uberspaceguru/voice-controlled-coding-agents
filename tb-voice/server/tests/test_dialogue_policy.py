"""Executable speech-act policy checks; providers and clocks are deterministic.

The corpus adapter injects bounded semantic answers, then checks actual decisions,
pending state, and irreversible commits. It does not claim model or audio accuracy.
Only development conversations are used here; live held-out evaluation is separate.
"""

import json
import unittest
from pathlib import Path

from dialogue import Decision, Dialogue, Record
from evals.dialogue_fixture import (
    Clock,
    begin_turn,
    choice,
    contract_errors,
    mocked_judgment,
    policy_observation,
    seed_dialogue,
)
from evals.dialogue_fixture import semantic_answers as answers

TARGETS = [
    {"sessionId": "alpha", "name": "Alpha", "cwd": "/work/demo/api"},
    {"sessionId": "beta", "name": "Beta", "cwd": "/work/demo/web"},
]


class DialoguePolicy(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.dialogue = Dialogue(self.clock)
        self.dialogue.sync("alpha", TARGETS)

    def decide(self, text, **kwargs):
        epoch = self.dialogue.begin()
        return self.dialogue.decide(text, answers(**kwargs), epoch)

    def offered(self, text="Run only the API tests.", target="alpha"):
        self.dialogue.begin()
        decision = self.dialogue.prepare(text, target)
        self.dialogue.mark_offered(decision.pending_id)
        return decision

    def test_explicit_work_commits_source_verbatim_once(self):
        text = "Tell Alpha to run only tests.test_api; leave the browser checks alone."
        decision = self.decide(text, target="alpha")
        self.assertEqual(decision.op, "dispatch")
        record = self.dialogue.commit(decision)
        self.assertEqual((record.text, record.target, record.status), (text, "alpha", "dispatching"))
        self.assertIsNone(self.dialogue.commit(decision))

    def test_confidence_is_not_execution_permission(self):
        epoch = self.dialogue.begin()
        result = answers()
        result["execute"] = {"noul": 0.4, "confidence": 1.0}
        result["act"] = choice("direct", 0.7)
        decision = self.dialogue.decide("Run the API tests.", result, epoch)
        self.assertEqual(decision.op, "clarify")
        self.assertIsNone(self.dialogue.last_action)
        self.assertFalse(self.dialogue.pending.offered)

    def test_low_probability_source_cannot_authorize_dispatch(self):
        epoch = self.dialogue.begin()
        result = answers()
        result["source"] = {
            "choice": "utterance", "confidence": 1.0,
            "probabilities": {"utterance": 0.51, "last_command": 0.49},
        }
        decision = self.dialogue.decide("Send that command.", result, epoch)
        self.assertNotEqual(decision.op, "dispatch")
        self.assertIsNone(self.dialogue.last_action)

    def test_unknown_and_uncertain_target_require_clarification(self):
        for target, p in [("missing-agent", 0.99), ("alpha", 0.4), ("ambiguous", 0.99)]:
            with self.subTest(target=target, probability=p):
                self.setUp()
                epoch = self.dialogue.begin()
                result = answers(target=target)
                result["target"] = choice(target, p)
                decision = self.dialogue.decide("Run the unit tests.", result, epoch)
                self.assertEqual(decision.op, "clarify")
                self.assertIsNone(self.dialogue.pending.target)
                self.assertEqual(self.dialogue.pending.status, "target")

    def test_recorded_command_is_offered_before_execution(self):
        command = "python -m unittest tests.test_api"
        self.dialogue.command(command, "alpha")
        first = self.decide("Send that command.", source="last_command")
        self.assertEqual(first.op, "clarify")
        self.assertEqual(self.dialogue.pending.text, command)
        self.assertFalse(self.dialogue.pending.offered)
        self.dialogue.mark_offered(first.pending_id)
        second = self.decide("Yes, send it.", act="confirm", source="pending")
        record = self.dialogue.commit(second)
        self.assertEqual((record.target, record.text), ("alpha", command))
        self.assertIsNone(self.dialogue.pending)
        third = self.decide("Yes.", act="confirm", source="unknown", target="none")
        self.assertNotEqual(third.op, "dispatch")

    def test_proposal_reference_preserves_exact_source_words(self):
        source = "Run the API tests, then report the exit code."
        self.dialogue.offer_proposal(source, "beta")
        decision = self.decide("Do that.", target="none", source="proposal")
        self.assertEqual(decision.op, "clarify")
        self.assertEqual((self.dialogue.pending.text, self.dialogue.pending.target), (source, "beta"))
        self.assertIsNone(self.dialogue.last_action)

    def test_stale_or_missing_references_never_dispatch(self):
        for source in ("last_command", "proposal", "unknown"):
            with self.subTest(source=source):
                self.setUp()
                self.dialogue.command("Run the API tests.", "alpha")
                self.clock.advance(91)
                decision = self.decide("Do that.", source=source)
                self.assertEqual(decision.op, "clarify")
                self.assertIsNone(self.dialogue.last_action)
                self.assertIsNone(self.dialogue.pending)

    def test_repeated_unresolved_target_stops_after_one_focused_clarification(self):
        first = self.decide("Have that agent review the check.", target="ambiguous")
        self.assertEqual(first.op, "clarify")
        self.assertIsNotNone(self.dialogue.pending)
        repeated = self.decide("Have that agent review the check.", target="ambiguous")
        self.assertEqual((repeated.op, repeated.reason), ("say", "clarification_exhausted"))
        self.assertIsNone(self.dialogue.pending)
        self.assertIsNone(self.dialogue.commit(repeated))
        self.assertIsNone(self.dialogue.last_action)

    def test_changed_target_correction_gets_a_new_confirmation(self):
        command = "Review the timeout check."
        self.dialogue.command(command, "alpha")
        first = self.decide("Send the previous instruction.", source="last_command")
        self.assertEqual(first.op, "clarify")
        self.assertEqual(self.dialogue.pending.target, "alpha")
        self.dialogue.mark_offered(first.pending_id)
        repaired = self.decide(
            "The other agent should receive that, Beta.", act="correct", target="beta",
            source="pending", response="clarification", execute=0.01,
        )
        self.assertEqual(repaired.op, "clarify")
        self.assertNotEqual(repaired.reason, "clarification_exhausted")
        self.assertEqual((self.dialogue.pending.target, self.dialogue.pending.text), ("beta", command))
        self.assertFalse(self.dialogue.pending.offered)
        self.assertIsNone(self.dialogue.last_action)
        self.dialogue.mark_offered(repaired.pending_id)
        confirmed = self.decide("Yes, send that to Beta.", act="confirm", target="beta", source="pending")
        record = self.dialogue.commit(confirmed)
        self.assertEqual((record.target, record.text), ("beta", command))

    def test_target_repair_binds_unsent_request_without_dispatch(self):
        self.offered("Run only tests.test_api.")
        repair = self.decide("No, I meant Beta.", act="correct", target="beta", source="pending")
        self.assertEqual(repair.op, "clarify")
        self.assertEqual((self.dialogue.pending.target, self.dialogue.pending.text), ("beta", "Run only tests.test_api."))
        self.assertFalse(self.dialogue.pending.offered)
        self.assertIsNone(self.dialogue.last_action)
        unoffered = self.decide("Yes.", act="confirm", source="pending")
        self.assertNotEqual(unoffered.op, "dispatch")
        self.dialogue.mark_offered(repair.pending_id)
        confirmed = self.decide("Yes.", act="confirm", target="beta", source="pending")
        record = self.dialogue.commit(confirmed)
        self.assertEqual((record.target, record.text), ("beta", "Run only tests.test_api."))

    def test_confirmation_cannot_override_independent_source_or_target_contradictions(self):
        cases = (
            ("utterance", 0.98, "stage", 0.98),
            ("pending", 0.98, "beta", 0.4),
            ("pending", 0.98, "beta", 0.98),
            ("pending", 0.4, "stage", 0.98),
        )
        for source, source_p, target, target_p in cases:
            with self.subTest(source=source, source_p=source_p, target=target, target_p=target_p):
                self.setUp()
                self.offered("Run only Alpha's API tests.", "alpha")
                pending = self.dialogue.pending
                epoch = self.dialogue.begin()
                result = answers(act="confirm", source=source, target=target, execute=1.0)
                result["act"] = choice("confirm", 1.0)
                result["source"] = choice(source, source_p)
                result["target"] = choice(target, target_p)
                decision = self.dialogue.decide("Yes, do it.", result, epoch)
                self.assertEqual((decision.op, decision.response), ("clarify", "clarification"))
                self.assertIsNone(self.dialogue.commit(decision))
                self.assertIsNone(self.dialogue.last_action)
                self.assertIs(self.dialogue.pending, pending)
                self.assertEqual(pending.target, "alpha")

    def test_instruction_repair_keeps_bound_source_and_correction(self):
        self.offered("Run the test suite.")
        repair = self.decide("Actually, only run the API tests.", act="correct", source="utterance")
        self.assertEqual(repair.op, "clarify")
        self.assertIn("Run the test suite.", self.dialogue.pending.text)
        self.assertIn("Actually, only run the API tests.", self.dialogue.pending.text)
        self.assertFalse(self.dialogue.pending.offered)
        self.assertIsNone(self.dialogue.last_action)

    def test_repair_after_dispatch_does_not_replay_or_promise_undo(self):
        decision = self.decide("Tell Alpha to run the tests.", target="alpha")
        record = self.dialogue.commit(decision)
        record.status = "sent"
        repair = self.decide("No, I meant the other agent.", act="correct", target="beta", source="pending")
        self.assertNotEqual(repair.op, "dispatch")
        self.assertIn("cannot", repair.text.lower())
        self.assertIn("nothing was resent", repair.text.lower())
        self.assertIsNone(self.dialogue.pending)
        self.assertIs(self.dialogue.last_action, record)

    def test_read_only_repair_cancels_unsent_execution(self):
        self.offered("python -m unittest tests.test_api")
        decision = self.decide(
            "Don't run it, just tell me the command.", act="correct", source="pending",
            response="exact_command", route="exact_command", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response), ("answer", "exact_command"))
        self.assertEqual(decision.recorded_text, "python -m unittest tests.test_api")
        self.assertIsNone(self.dialogue.pending)
        self.assertIsNone(self.dialogue.last_action)

    def test_formatting_repair_after_information_is_not_blocked_by_old_sent_work(self):
        for age in (5, 600):
            with self.subTest(sent_action_age=age):
                self.setUp()
                sent = Record("Inspect the API timeout.", "alpha", self.clock() - age, "sent")
                self.dialogue.last_action = sent
                information = self.decide(
                    "Where is the project?", act="inform", source="utterance",
                    response="summary", route="custom", execute=0.01,
                )
                self.assertEqual(information.op, "answer")
                repair = self.decide(
                    "Actually, use the full path.", act="correct", source="utterance",
                    response="exact_directory", route="exact_directory", execute=0.01,
                )
                self.assertEqual((repair.op, repair.response, repair.target), ("answer", "exact_directory", "alpha"))
                self.assertNotEqual(repair.reason, "cannot_undo")
                self.assertEqual(repair.recorded_text, "")
                self.assertIs(self.dialogue.last_action, sent)
                self.assertIsNone(self.dialogue.commit(repair))

    def test_exact_directory_repair_cannot_speak_the_pending_command_as_a_path(self):
        self.offered("python -m unittest tests.test_api", "alpha")
        decision = self.decide(
            "Actually, use the full path.", act="correct", source="pending",
            response="exact_directory", route="exact_directory", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response, decision.target), ("answer", "exact_directory", "alpha"))
        self.assertEqual(decision.recorded_text, "")
        self.assertIsNone(self.dialogue.pending)
        self.assertIsNone(self.dialogue.commit(decision))
        self.assertIsNone(self.dialogue.last_action)

    def test_directory_regression_stays_read_only_under_wrong_action_answer(self):
        decision = self.decide("Give me the full directory path.")
        self.assertEqual((decision.op, decision.response, decision.target), ("answer", "exact_directory", "alpha"))
        self.assertIsNone(self.dialogue.last_action)
        self.assertIsNone(self.dialogue.pending)

    def test_command_question_never_dispatches_the_recorded_command(self):
        self.dialogue.command("python -m unittest tests.test_api", "alpha")
        decision = self.decide(
            "What command did you send?", act="inform", source="last_command",
            response="exact_command", route="exact_command", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response), ("answer", "exact_command"))
        self.assertIsNone(self.dialogue.pending)
        self.assertIsNone(self.dialogue.last_action)

    def test_uncertain_concrete_information_target_never_falls_back_to_stage(self):
        epoch = self.dialogue.begin()
        result = answers(
            act="inform", target="beta", response="exact_directory",
            route="exact_directory", execute=0.01,
        )
        result["target"] = choice("beta", 0.4)
        decision = self.dialogue.decide("What directory is Beta using?", result, epoch)
        self.assertEqual((decision.op, decision.response), ("clarify", "clarification"))
        self.assertIsNone(decision.target)
        self.assertEqual(decision.recorded_text, "")
        self.assertIsNone(self.dialogue.last_action)

    def test_unknown_command_source_never_reads_an_arbitrary_available_record(self):
        self.dialogue.command("npm run test", "alpha")
        self.offered("python -m unittest tests.test_api")
        decision = self.decide(
            "What was that command?", act="inform", source="unknown",
            response="exact_command", route="exact_command", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response), ("clarify", "clarification"))
        self.assertEqual(decision.recorded_text, "")
        self.assertIsNone(self.dialogue.last_action)

    def test_sent_request_source_preserves_original_text_within_freshness_window(self):
        original = "Tell Alpha to run only API tests; leave browser checks alone."
        for age in (0, 89, 90):
            with self.subTest(age=age):
                self.setUp()
                self.dialogue.last_action = Record(original, "alpha", self.clock() - age, "sent")
                decision = self.decide(
                    "What exactly did you send to Alpha?", act="inform", target="alpha",
                    source="last_action", response="exact_command", route="exact_command",
                    execute=0.01,
                )
                self.assertEqual((decision.op, decision.response, decision.target), ("answer", "exact_command", "alpha"))
                self.assertEqual(decision.recorded_text, original)
                self.assertIsNone(self.dialogue.pending)
                self.assertIsNone(self.dialogue.commit(decision))

    def test_unsent_or_uncertain_delivery_is_not_reported_as_a_sent_command(self):
        for status in ("dispatching", "waiting", "failed", "unknown", "recorded"):
            with self.subTest(status=status):
                self.setUp()
                self.dialogue.last_action = Record("Run only API tests.", "alpha", self.clock(), status)
                decision = self.decide(
                    "What exactly did you send?", act="inform", source="last_action",
                    response="exact_command", route="exact_command", execute=0.01,
                )
                self.assertEqual((decision.op, decision.response, decision.reason), ("say", "receipt", "sent_record_missing"))
                self.assertEqual(decision.recorded_text, "")
                self.assertIsNone(self.dialogue.pending)

    def test_stale_sent_request_never_falls_back_to_a_newer_reported_command(self):
        self.dialogue.last_action = Record("Run yesterday's check.", "alpha", self.clock() - 90.01, "sent")
        self.dialogue.command("npm run unrelated-check", "alpha")
        decision = self.decide(
            "What exactly did you send?", act="inform", source="last_action",
            response="exact_command", route="exact_command", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response, decision.reason), ("say", "receipt", "sent_record_missing"))
        self.assertEqual(decision.recorded_text, "")
        self.assertIsNone(self.dialogue.pending)

    def test_pending_and_reported_commands_cannot_replace_last_dispatched_request(self):
        original = "Tell Alpha to inspect the API timeout without editing files."
        sent = Record(original, "alpha", self.clock(), "sent")
        self.dialogue.last_action = sent
        self.dialogue.command("python -m unittest tests.test_api", "alpha")
        self.offered("npm run build")
        decision = self.decide(
            "Read the message that you actually sent.", act="inform", source="last_action",
            response="exact_command", route="exact_command", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response), ("answer", "exact_command"))
        self.assertEqual(decision.recorded_text, original)
        self.assertIs(self.dialogue.last_action, sent)
        self.assertIsNone(self.dialogue.commit(decision))

    def test_sent_request_target_must_match_the_information_request(self):
        self.dialogue.last_action = Record("Inspect the API timeout.", "alpha", self.clock(), "sent")
        decision = self.decide(
            "What did you send to Beta?", act="inform", target="beta", source="last_action",
            response="exact_command", route="exact_command", execute=0.01,
        )
        self.assertEqual((decision.op, decision.response, decision.reason), ("say", "receipt", "sent_record_missing"))
        self.assertEqual(decision.recorded_text, "")
        self.assertIsNone(self.dialogue.pending)

    def test_reference_selection_cannot_skip_confirmation_even_at_full_probability(self):
        for source in ("proposal", "last_command"):
            with self.subTest(source=source):
                self.setUp()
                record = Record("Run only tests.test_api.", "alpha", self.clock())
                setattr(self.dialogue, source, record)
                epoch = self.dialogue.begin()
                result = answers(source=source, addressed=1.0, execute=1.0)
                for question in ("act", "source", "target"):
                    result[question] = choice(result[question]["choice"], 1.0)
                decision = self.dialogue.decide("Do that right now.", result, epoch)
                self.assertEqual(decision.op, "clarify")
                self.assertEqual(self.dialogue.pending.text, record.text)
                self.assertFalse(self.dialogue.pending.offered)
                self.assertIsNone(self.dialogue.commit(decision))
                self.assertIsNone(self.dialogue.last_action)

    def test_bare_confirmation_of_reference_makes_offer_instead_of_dispatching(self):
        for source in ("proposal", "last_command"):
            with self.subTest(source=source):
                self.setUp()
                record = Record("Run only tests.test_api.", "alpha", self.clock())
                setattr(self.dialogue, source, record)
                decision = self.decide("Yes, do that.", act="confirm", source=source)
                self.assertEqual(decision.op, "clarify")
                self.assertEqual(self.dialogue.pending.text, record.text)
                self.assertFalse(self.dialogue.pending.offered)
                self.assertIsNone(self.dialogue.commit(decision))
                self.assertIsNone(self.dialogue.last_action)

    def test_acknowledgment_does_not_consume_a_fresh_offer(self):
        self.offered()
        pending = self.dialogue.pending
        for text in ("Okay.", "Mm-hmm.", "Sounds good."):
            with self.subTest(text=text):
                decision = self.decide(text, act="ack", target="none", source="unknown")
                self.assertEqual(decision.op, "silent")
                self.assertIs(self.dialogue.pending, pending)
                self.assertIsNone(self.dialogue.last_action)

    def test_side_conversation_and_quoted_speech_stay_silent(self):
        for text in (
            'Sam said "Tranquility, deploy it".',
            "Sam, can you send that command to Alpha?",
            "Maybe the tests would explain it.",
        ):
            with self.subTest(text=text):
                decision = self.decide(text, act="think", addressed=0.01, execute=0.01)
                self.assertEqual(decision.op, "silent")
                self.assertIsNone(self.dialogue.last_action)

    def test_missing_expired_unoffered_or_held_proposal_cannot_be_confirmed(self):
        for mode in ("missing", "expired", "unoffered", "held"):
            with self.subTest(mode=mode):
                self.setUp()
                if mode != "missing":
                    self.offered()
                    if mode == "expired":
                        self.clock.advance(46)
                    elif mode == "unoffered":
                        self.dialogue.pending.offered = False
                    elif mode == "held":
                        self.dialogue.pending.status = "held"
                decision = self.decide("Yes, go ahead.", act="confirm", source="pending")
                self.assertNotEqual(decision.op, "dispatch")
                self.assertIsNone(self.dialogue.last_action)

    def test_hold_resume_reoffers_and_still_requires_confirmation(self):
        self.offered()
        hold = self.decide("Hold that.", act="control", route="hold", source="pending")
        self.assertEqual(hold.op, "say")
        self.assertEqual(self.dialogue.pending.status, "held")
        self.assertFalse(self.dialogue.pending.offered)
        resume = self.decide("Resume that request.", act="control", route="resume", source="pending")
        self.assertEqual(resume.op, "clarify")
        self.assertFalse(self.dialogue.pending.offered)
        self.assertIsNone(self.dialogue.last_action)

    def test_pause_does_not_resume_or_dispatch_held_work(self):
        self.offered()
        pause = self.decide("Pause listening.", act="control", route="pause_listening")
        self.assertEqual(pause.op, "say")
        self.assertEqual((self.dialogue.listening, self.dialogue.pending.status), ("paused", "held"))
        work = self.decide("Run the API tests.")
        self.assertEqual(work.op, "silent")
        resume = self.decide("Resume listening.", act="control", route="resume_listening")
        self.assertEqual(resume.op, "say")
        self.assertEqual(self.dialogue.listening, "active")
        self.assertEqual(self.dialogue.pending.status, "held")
        self.assertIsNone(self.dialogue.last_action)

    def test_stop_speaking_keeps_unsent_request_and_running_task_separate(self):
        self.offered()
        self.dialogue.last_action = Record("Run tests.", "alpha", self.clock(), "sent")
        pending, action = self.dialogue.pending, self.dialogue.last_action
        decision = self.decide("Stop speaking.", act="control", route="stop_speaking")
        self.assertEqual(decision.op, "mute")
        self.assertIs(self.dialogue.pending, pending)
        self.assertIs(self.dialogue.last_action, action)

    def test_cancel_and_reject_clear_unsent_work(self):
        for act, text in (("cancel", "Never mind."), ("reject", "No.")):
            with self.subTest(act=act):
                self.offered()
                decision = self.decide(text, act=act, source="pending")
                self.assertEqual(decision.reason, "pending_canceled")
                self.assertIsNone(self.dialogue.pending)
                self.assertIsNone(self.dialogue.last_action)
                again = self.decide("Yes.", act="confirm", source="pending")
                self.assertNotEqual(again.op, "dispatch")

    def test_cancel_already_dispatched_work_is_truthful(self):
        sent = self.dialogue.commit(self.decide("Run the API tests."))
        sent.status = "sent"
        canceled = self.decide("Never mind.", act="cancel")
        self.assertEqual(canceled.reason, "cannot_undo")
        self.assertIn("cannot undo", canceled.text.lower())
        self.assertIs(self.dialogue.last_action, sent)

    def test_duplicate_transcript_key_is_not_a_second_turn(self):
        first = self.dialogue.begin("transcript-42")
        decision = self.dialogue.decide("Run the API tests.", answers(), first)
        self.assertIsNotNone(self.dialogue.commit(decision))
        self.assertIsNone(self.dialogue.begin("transcript-42"))
        self.assertEqual(self.dialogue.epoch, first)

    def test_cancel_during_classification_invalidates_old_result(self):
        old = self.dialogue.begin("work-1")
        cancel = self.decide("Never mind.", act="cancel")
        self.assertNotEqual(cancel.op, "dispatch")
        late = self.dialogue.decide("Run the tests.", answers(), old)
        self.assertEqual((late.op, late.reason), ("silent", "stale_judgment"))
        self.assertIsNone(self.dialogue.commit(late))

    def test_cancel_between_decision_and_dispatch_invalidates_commit(self):
        pending_dispatch = self.decide("Run the API tests.")
        self.decide("Never mind.", act="cancel")
        self.assertIsNone(self.dialogue.commit(pending_dispatch))
        self.assertIsNone(self.dialogue.last_action)

    def test_stage_switch_invalidates_confirmation_and_read_context(self):
        self.offered()
        epoch, old_stage = self.dialogue.epoch, self.dialogue.stage
        self.dialogue.command("python -m unittest tests.test_api", "alpha")
        self.dialogue.sync("beta", TARGETS)
        self.assertFalse(self.dialogue.valid(epoch, old_stage))
        self.assertIsNone(self.dialogue.pending)
        self.assertIsNone(self.dialogue.last_command)
        decision = self.decide("Yes.", act="confirm", source="pending")
        self.assertNotEqual(decision.op, "dispatch")

    def test_stage_switch_invalidates_explicit_dispatch_decision(self):
        decision = self.decide("Tell Alpha to run the API tests.", target="alpha")
        self.dialogue.sync("beta", TARGETS)
        self.assertIsNone(self.dialogue.commit(decision))
        self.assertIsNone(self.dialogue.last_action)

    def test_stored_offer_from_another_stage_cannot_be_confirmed(self):
        self.offered()
        # Exercise the invariant even if a restored/offered record bypassed sync.
        self.dialogue.stage = "beta"
        decision = self.decide("Yes, send it.", act="confirm", source="pending")
        self.assertIsNone(self.dialogue.commit(decision))
        self.assertIsNone(self.dialogue.last_action)

    def test_short_new_request_cannot_commit_an_old_confirmed_action(self):
        self.offered()
        old = self.decide("Yes.", act="confirm", source="pending")
        question = self.decide(
            "What branch are you on?", act="inform", response="exact_branch",
            route="exact_branch", execute=0.01,
        )
        self.assertEqual(question.op, "answer")
        self.assertIsNone(self.dialogue.commit(old))

    def test_disappearing_target_invalidates_confirmed_dispatch(self):
        self.offered(target="beta")
        decision = self.decide("Yes.", act="confirm", target="beta", source="pending")
        self.dialogue.sync("alpha", [TARGETS[0]])
        self.assertIsNone(self.dialogue.commit(decision))

    def test_only_dispatch_decisions_can_cross_commit_boundary(self):
        epoch = self.dialogue.begin()
        for op in ("answer", "clarify", "say", "silent", "mute"):
            with self.subTest(op=op):
                decision = Decision(op, "not_a_command", text="Run tests.", target="alpha", epoch=epoch)
                self.assertIsNone(self.dialogue.commit(decision))
                self.assertIsNone(self.dialogue.last_action)

    def test_confirmed_action_cannot_be_committed_after_offer_expires(self):
        self.offered()
        decision = self.decide("Yes.", act="confirm", source="pending")
        self.clock.advance(46)
        self.assertIsNone(self.dialogue.commit(decision))
        self.assertIsNone(self.dialogue.last_action)


class DevelopmentCorpus(unittest.TestCase):
    """Provider-independent contract replay, not a classifier accuracy score."""

    def test_development_conversations_assert_execution_and_pending_state(self):
        corpus = json.loads((Path(__file__).parents[1] / "evals/dialogue_conversations.json").read_text())
        count = 0
        for conversation in corpus["conversations"]:
            if conversation["split"] != "development":
                continue
            for turn in conversation["turns"]:
                count += 1
                with self.subTest(turn=turn["id"], text=turn["text"]):
                    dialogue = seed_dialogue(turn, corpus["fixtures"])
                    epoch = begin_turn(dialogue, turn)
                    observed = policy_observation(dialogue, turn, mocked_judgment(turn), epoch)
                    self.assertEqual(contract_errors(turn, observed), [], observed)
        self.assertGreaterEqual(count, 40)


if __name__ == "__main__":
    unittest.main()
