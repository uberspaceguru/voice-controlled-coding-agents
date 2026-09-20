import unittest

from conversation_memory import Memory


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ConversationMemory(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.memory = Memory(self.clock)

    def fact(self, value="/demo/alpha", target="alpha", source="brief-7"):
        return self.memory.observe(source, target, {"value": value})

    def question(self, kind="directory", target="alpha"):
        return self.memory.ask("Read the exact value.", target, kind)

    def test_exact_answer_requires_copied_value_and_completed_output(self):
        question, source = self.question(), self.fact()
        for status in ("acknowledged", "generated", "unknown", "interrupted"):
            with self.subTest(status=status):
                self.assertFalse(self.memory.answer(question, source, "/demo/alpha", status, expected="/demo/alpha", delivery_id="audio-1"))
                self.assertIs(self.memory.unresolved("alpha"), question)
        self.assertTrue(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha", delivery_id="audio-1"))
        self.assertEqual(question.status, "resolved")
        self.assertIsNone(self.memory.unresolved("alpha"))

    def test_mismatching_missing_or_invented_expected_value_never_resolves(self):
        cases = [
            ("/demo/alpha", None), ("/demo/alpha", ""), ("A file path", "/demo/alpha"),
            ("/demo/alpha ", "/demo/alpha"), ("/invented", "/invented"),
        ]
        for text, expected in cases:
            with self.subTest(text=text, expected=expected):
                self.setUp()
                question, source = self.question(), self.fact()
                self.assertFalse(self.memory.answer(question, source, text, "output_complete", expected=expected))
                self.assertIs(self.memory.unresolved("alpha"), question)

    def test_prose_substring_or_dictionary_key_is_not_exact_source_evidence(self):
        for payload in ({"message": "Directory: /demo/alpha"}, {"/demo/alpha": "not the value"}):
            with self.subTest(payload=payload):
                question = self.question()
                source = self.memory.observe("brief", "alpha", payload)
                self.assertFalse(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
                self.assertIs(self.memory.unresolved("alpha"), question)

    def test_nested_copied_scalar_resolves_and_preserves_source_provenance(self):
        question = self.question("command")
        self.clock.advance(2)
        source = self.memory.observe("transcript-line-19", "alpha", {"facts": [{"command": "python -m unittest"}]})
        self.clock.advance(3)
        self.assertTrue(self.memory.answer(question, source, "python -m unittest", "output_complete", expected="python -m unittest", delivery_id="tts-19"))
        self.assertEqual(question.source_id, "transcript-line-19")
        self.assertEqual(question.source_observed_at, 1002.0)
        self.assertEqual(question.resolved_at, 1005.0)
        self.assertEqual(question.delivery_id, "tts-19")

    def test_acknowledgment_is_separate_from_answer_delivery(self):
        question = self.question()
        self.assertTrue(self.memory.acknowledged(question))
        self.assertEqual(question.acknowledgement_at, self.clock())
        self.assertEqual(question.status, "open")
        self.assertIs(self.memory.unresolved("alpha"), question)
        self.assertIsNone(question.resolved_at)

    def test_resolved_fresh_question_can_be_acknowledged_without_changing_resolution(self):
        question, source = self.question(), self.fact()
        self.assertTrue(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        resolved_at = question.resolved_at
        self.clock.advance(2)
        self.assertTrue(self.memory.acknowledged(question))
        self.assertEqual(question.acknowledgement_at, self.clock())
        self.assertEqual((question.status, question.resolved_at), ("resolved", resolved_at))
        self.assertIsNone(self.memory.unresolved("alpha"))
        self.clock.advance(299)
        self.assertFalse(self.memory.acknowledged(question))
        self.assertEqual(question.status, "resolved")

    def test_generic_output_remains_relevance_unverified(self):
        question = self.memory.ask("Why was the test slow?", "alpha", "explanation")
        source = self.memory.observe("brief", "alpha", {"result": "The cache was cold."})
        self.assertFalse(self.memory.answer(question, source, "A reason the model supplied.", "output_complete", delivery_id="generic-1"))
        self.assertEqual(question.status, "relevance_unverified")
        self.assertIs(self.memory.unresolved("alpha"), question)
        self.assertIsNone(question.resolved_at)
        self.assertTrue(self.memory.claim_repair(question))

    def test_each_question_has_only_one_automatic_repair_claim(self):
        question = self.question()
        self.assertTrue(self.memory.claim_repair(question))
        self.assertFalse(self.memory.claim_repair(question))
        self.assertEqual(question.repair_count, 1)
        replacement = self.memory.ask("Use the whole path this time.", "alpha", "directory")
        self.assertFalse(self.memory.claim_repair(question))
        self.assertTrue(self.memory.claim_repair(replacement))

    def test_exact_correction_supersedes_only_same_target_and_kind(self):
        original = self.question()
        branch = self.question("branch")
        other = self.question(target="beta")
        replacement = self.memory.ask("Read every directory component.", "alpha", "directory")
        self.assertEqual((original.status, original.superseded_by), ("superseded", replacement.id))
        self.assertEqual(branch.status, "open")
        self.assertEqual(other.status, "open")
        self.assertIs(self.memory.unresolved("alpha"), replacement)
        self.assertFalse(self.memory.answer(original, self.fact(), "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.assertIs(self.memory.unresolved("beta"), other)

    def test_source_for_other_agent_cannot_close_question(self):
        question = self.question()
        source = self.fact(target="beta")
        self.assertFalse(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.assertIs(self.memory.unresolved("alpha"), question)

    def test_cancel_question_affects_only_latest_unanswered_question_for_target(self):
        earlier = self.question("branch")
        latest = self.question("directory")
        other = self.question("directory", target="beta")
        canceled = self.memory.cancel_question("alpha", "turn-cancel")
        self.assertIs(canceled, latest)
        self.assertEqual((latest.status, latest.cancellation_source_id, latest.canceled_at), ("canceled", "turn-cancel", self.clock()))
        self.assertIs(self.memory.unresolved("alpha"), earlier)
        self.assertIs(self.memory.unresolved("beta"), other)
        self.assertEqual((earlier.status, other.status), ("open", "open"))

    def test_cancel_preserves_answer_provenance_and_prevents_late_resolution_or_repair(self):
        question, source = self.question(), self.fact(source="brief-before-cancel")
        self.memory.answer(question, source, "/demo/alpha", "interrupted", expected="/demo/alpha")
        self.clock.advance(1)
        self.assertIs(self.memory.cancel_question("alpha", "turn-later-cancel"), question)
        self.assertEqual(question.source_id, "brief-before-cancel")
        self.assertEqual(question.source_observed_at, source.created)
        self.assertEqual(question.cancellation_source_id, "turn-later-cancel")
        self.assertFalse(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.assertFalse(self.memory.claim_repair(question))
        self.assertFalse(self.memory.acknowledged(question))
        self.assertEqual(self.memory.resume("alpha", "Alpha"), "Back with Alpha.")

    def test_cancel_can_close_unverified_generic_question_but_not_expired_or_resolved(self):
        generic = self.memory.ask("What caused it?", "alpha", "information")
        self.memory.answer(generic, self.fact(), "A generated explanation.", "output_complete")
        self.assertEqual(generic.status, "relevance_unverified")
        self.assertIs(self.memory.cancel_question("alpha", "cancel-generic"), generic)
        self.assertIsNone(self.memory.cancel_question("alpha", "cancel-again"))
        expired = self.question()
        self.clock.advance(301)
        self.assertIsNone(self.memory.cancel_question("alpha", "cancel-expired"))
        self.assertEqual(expired.status, "expired")
        question, source = self.question(), self.fact()
        self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha")
        self.assertIsNone(self.memory.cancel_question("alpha", "cancel-resolved"))
        self.assertEqual(question.status, "resolved")

    def test_five_minute_boundary_for_question_and_observation(self):
        question, source = self.question(), self.fact()
        self.clock.advance(300)
        self.assertTrue(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.setUp()
        question, source = self.question(), self.fact()
        self.clock.advance(300.01)
        self.assertFalse(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.assertEqual(question.status, "expired")
        self.assertIsNone(self.memory.unresolved("alpha"))
        self.assertFalse(self.memory.should_update(source))

    def test_fresh_question_cannot_be_answered_from_expired_source(self):
        source = self.fact()
        self.clock.advance(301)
        question = self.question()
        self.assertFalse(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.assertIs(self.memory.unresolved("alpha"), question)

    def test_payload_signature_is_stable_and_snapshot_cannot_be_mutated(self):
        payload = {"status": "waiting", "details": {"count": 2}}
        source = self.memory.observe("brief", "alpha", payload)
        same = self.memory.observe("brief-next-read", "alpha", {"details": {"count": 2}, "status": "waiting"})
        self.assertEqual(source.signature, same.signature)
        payload["details"]["count"] = 99
        copy = source.payload
        copy["details"]["count"] = 100
        self.assertEqual(source.payload["details"]["count"], 2)
        self.assertEqual(source.source_id, "brief")
        self.assertEqual(source.created, 1000.0)

    def test_repeat_suppression_begins_only_after_actual_completed_update(self):
        source = self.fact()
        for status in ("acknowledged", "generated", "unknown", "interrupted"):
            with self.subTest(status=status):
                self.assertFalse(self.memory.delivered_update(source, "The path is ready.", status, delivery_id="update-1"))
                self.assertTrue(self.memory.should_update(source))
        self.assertTrue(self.memory.delivered_update(source, "The path is ready.", "output_complete", delivery_id="update-1"))
        self.assertFalse(self.memory.should_update(source))
        self.assertFalse(self.memory.delivered_update(source, "The path is ready.", "output_complete", delivery_id="update-1"))

    def test_identical_text_with_changed_source_payload_is_not_suppressed(self):
        first = self.memory.observe("brief", "alpha", {"tests": 2, "result": "passed"})
        self.memory.delivered_update(first, "Tests passed.", "output_complete")
        changed = self.memory.observe("brief", "alpha", {"tests": 3, "result": "passed"})
        self.assertTrue(self.memory.should_update(changed))
        self.memory.delivered_update(changed, "Tests passed.", "interrupted")
        self.assertTrue(self.memory.should_update(changed))

    def test_identical_payload_is_scoped_per_agent(self):
        first, other = self.fact(), self.fact(target="beta")
        self.memory.delivered_update(first, "Path ready.", "output_complete")
        self.assertFalse(self.memory.should_update(self.fact(source="next-read")))
        self.assertTrue(self.memory.should_update(other))

    def test_critical_failure_or_pending_decision_bypasses_repeat_suppression(self):
        for payload in ({"status": "failed"}, {"status": "needs_decision"}):
            with self.subTest(payload=payload):
                source = self.memory.observe("status", "alpha", payload)
                self.memory.delivered_update(source, "Needs your attention.", "output_complete")
                self.assertFalse(self.memory.should_update(source))
                self.assertTrue(self.memory.should_update(source, critical=True))

    def test_answer_and_update_can_share_audio_delivery_identity(self):
        question, source = self.question(), self.fact()
        self.assertTrue(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha", delivery_id="audio-1"))
        self.assertTrue(self.memory.delivered_update(source, "/demo/alpha", "output_complete", delivery_id="audio-1"))
        self.assertFalse(self.memory.should_update(source))

    def test_old_delivered_baseline_expires_before_a_fresh_observation(self):
        self.memory.delivered_update(self.fact(), "Path ready.", "output_complete")
        self.clock.advance(301)
        fresh = self.fact()
        self.assertTrue(self.memory.should_update(fresh))

    def test_capacity_bounds_questions_sources_baselines_and_delivery_ids(self):
        memory = Memory(self.clock, capacity=2)
        old_question = memory.ask("Which directory?", "old", "directory")
        old_source = memory.observe("old-source", "old", {"value": "/demo/old"})
        for i in range(12):
            target = f"agent-{i}"
            memory.ask("Which directory?", target, "directory")
            source = memory.observe(f"source-{i}", target, {"value": f"/demo/{i}"})
            memory.delivered_update(source, "Path ready.", "output_complete", delivery_id=f"audio-{i}")
        self.assertEqual(old_question.status, "evicted")
        self.assertIsNone(memory.unresolved("old"))
        self.assertFalse(memory.should_update(old_source))
        self.assertLessEqual(len(memory._questions), 2)
        self.assertLessEqual(len(memory._observations), 2)
        self.assertLessEqual(len(memory._baselines), 2)
        self.assertLessEqual(len(memory._deliveries), 8)

    def test_memory_does_not_accept_another_session_objects(self):
        first = Memory(self.clock)
        question = first.ask("Which directory?", "alpha", "directory")
        source = first.observe("brief", "alpha", {"value": "/demo/alpha"})
        self.assertIsNone(self.memory.unresolved("alpha"))
        self.assertFalse(self.memory.answer(question, source, "/demo/alpha", "output_complete", expected="/demo/alpha"))
        self.assertFalse(self.memory.should_update(source))

    def test_resume_names_unresolved_exact_question_and_truthful_sent_state(self):
        self.question("identifier")
        pending = {"target": "alpha", "created": self.clock(), "status": "held"}
        sent = {"target": "alpha", "created": self.clock(), "status": "sent", "text": "Run tests."}
        result = self.memory.resume("alpha", "Alpha", pending=pending, last_action=sent)
        self.assertIn("exact session identifier question is still unanswered", result)
        self.assertIn("unsent request is on hold", result)
        self.assertIn("request was sent; completion is not confirmed", result)
        self.assertNotIn("Run tests.", result)
        self.assertEqual(result, self.memory.resume("alpha", "Alpha", pending=pending, last_action=sent))

    def test_resume_ignores_other_agents_and_stale_pending_or_actions(self):
        self.question(target="beta")
        wrong = {"target": "beta", "created": self.clock(), "status": "sent"}
        self.assertEqual(self.memory.resume("alpha", "Alpha", last_action=wrong), "Back with Alpha.")
        pending = {"target": "alpha", "created": self.clock() - 45.01, "status": "confirm"}
        action = {"target": "alpha", "created": self.clock() - 90.01, "status": "sent"}
        self.assertEqual(self.memory.resume("alpha", "Alpha", pending=pending, last_action=action), "Back with Alpha.")

    def test_newer_generic_question_does_not_hide_unanswered_exact_fact_on_resume(self):
        self.question("directory")
        latest = self.memory.ask("Why did it fail?", "alpha", "explanation")
        self.assertIs(self.memory.unresolved("alpha"), latest)
        self.assertIn("exact directory question is still unanswered", self.memory.resume("alpha", "Alpha"))

    def test_resume_names_generic_unverified_answer_when_no_exact_question_remains(self):
        question = self.memory.ask("Why did the check fail?", "alpha", "explanation")
        source = self.fact()
        self.memory.answer(question, source, "A generated explanation.", "output_complete")
        result = self.memory.resume("alpha", "Alpha")
        self.assertIn("A previous question still needs a verified answer.", result)
        self.assertNotIn("exact", result)
        self.assertEqual(question.status, "relevance_unverified")

    def test_resume_records_not_sent_without_claiming_success(self):
        action = {"target": "alpha", "created": self.clock(), "status": "not_sent"}
        result = self.memory.resume("alpha", "Alpha", last_action=action)
        self.assertIn("The last request was not sent.", result)
        self.assertNotIn("The last request was sent", result)

    def test_explicit_decision_records_preserve_source_and_latest_timestamp(self):
        proposed = self.memory.remember_decision("alpha", "proposed", "turn-10", "Run the check.")
        self.clock.advance(3)
        canceled = self.memory.remember_decision("alpha", "canceled", "turn-11", "Cancel the unsent request.")
        self.assertEqual((proposed.status, proposed.source_id, proposed.created), ("proposed", "turn-10", 1000.0))
        self.assertEqual((canceled.status, canceled.source_id, canceled.created), ("canceled", "turn-11", 1003.0))
        self.assertIs(self.memory._decisions["alpha"], canceled)
        self.assertEqual(canceled.text, "Cancel the unsent request.")
        self.assertIn("last explicit decision canceled an unsent request", self.memory.resume("alpha", "Alpha"))

    def test_decision_resumption_is_scoped_and_does_not_claim_completed_work(self):
        for status, fragment in (("canceled", "canceled"), ("superseded", "superseded"), ("held", "on hold")):
            with self.subTest(status=status):
                self.memory.remember_decision("alpha", status, "turn-12")
                result = self.memory.resume("alpha", "Alpha")
                self.assertIn(fragment, result)
                self.assertNotIn("completed", result)
                self.assertNotIn("was sent", result)
                self.assertEqual(self.memory.resume("beta", "Beta"), "Back with Beta.")
        self.memory.remember_decision("alpha", "proposed", "turn-13")
        self.assertEqual(self.memory.resume("alpha", "Alpha"), "Back with Alpha.")

    def test_decision_records_are_bounded_expire_and_reject_unsupported_states(self):
        memory = Memory(self.clock, capacity=2)
        for target in ("alpha", "beta", "gamma"):
            memory.remember_decision(target, "held", "turn-hold")
        self.assertEqual(len(memory._decisions), 2)
        self.assertEqual(memory.resume("alpha", "Alpha"), "Back with Alpha.")
        self.clock.advance(301)
        self.assertEqual(memory.resume("beta", "Beta"), "Back with Beta.")
        self.assertEqual(len(memory._decisions), 0)
        with self.assertRaises(ValueError):
            memory.remember_decision("alpha", "completed", "unverified")
        with self.assertRaises(ValueError):
            memory.remember_decision("alpha", "sent", "wrong-state-channel")
        self.assertEqual(len(memory._decisions), 0)

    def test_resume_waiting_or_failed_never_claims_sent(self):
        for status, fragment in (("waiting", "waiting for delivery"), ("failed", "failed to send"), ("unknown", "unconfirmed")):
            with self.subTest(status=status):
                action = {"target": "alpha", "created": self.clock(), "status": status}
                result = self.memory.resume("alpha", "Alpha", last_action=action)
                self.assertIn(fragment, result)
                self.assertNotIn("was sent", result)

    def test_invalid_or_oversized_inputs_cannot_grow_memory(self):
        for payload in ({"value": "x" * 65537}, {"number": float("nan")}):
            with self.subTest(payload_size=len(str(payload))):
                with self.assertRaises(ValueError):
                    self.memory.observe("source", "alpha", payload)
        with self.assertRaises(ValueError):
            self.memory.ask("x" * 4097, "alpha", "directory")
        with self.assertRaises(ValueError):
            Memory(self.clock, capacity=0)
        self.assertEqual(len(self.memory._observations), 0)
        self.assertEqual(len(self.memory._questions), 0)


if __name__ == "__main__":
    unittest.main()
