"""Actual manager routing plus durable journal, with synthetic reasoning and audio.

Only provider/CLI/device boundaries are mocked. These tests prove state and
side effects, never audible playback or human comprehension.
"""

import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_memory_manager import SpeechEvidence, make_manager
from test_stop_control import protective_stop
from test_tmux_fleet import agent, pane, snapshot

from fleet import parse_fleet
from supervisor_memory import JournalError, SupervisorJournal
from supervisor_session import SupervisorError, SupervisorPlan, prepare_context, validate_plan


def choice(value):
    return {"choice": value, "probabilities": {value: 0.99}, "confidence": 0.99}


def judgment(act="inform", route="supervise", target="none", response="summary", source="utterance", execute=0.01):
    return {"addressed": {"noul": 0.99}, "execute": {"noul": execute},
            "act": choice(act), "route": choice(route), "target": choice(target),
            "response": choice(response), "source": choice(source)}


class SupervisorHandlers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.supervisor_events = self.patches.enter_context(patch("supervisor_manager.emit", AsyncMock()))
        for module in ("manager", "dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.run = self.patches.enter_context(patch("tools._run", AsyncMock(return_value=(0, ""))))
        self.native_run = self.patches.enter_context(patch("manager._run", AsyncMock(return_value=(0, ""))))
        self.fixture_number = 0

    async def asyncTearDown(self):
        self.native_run.assert_not_awaited()

    def fixture(self, *, directory=None, statuses=(), actual_text=None, enabled=True):
        self.fixture_number += 1
        m = make_manager()
        m._supervisor_journal = SupervisorJournal(directory or self.root / str(self.fixture_number)) if enabled else None
        m._supervisor_refresh_lock = asyncio.Lock()
        m._supervisor_watch = None
        m._tmux_fleet = AsyncMock(return_value=parse_fleet(snapshot([
            pane(1, [agent("alpha", "Alpha", 101)]), pane(2, [agent("beta", "Beta", 102)])])))
        m._brief = AsyncMock(side_effect=lambda sid: {
            "sessionId": sid, "eventId": 7, "goal": f"Review {sid} checks.",
            "recap": f"Recorded progress for {sid}.", "proposal": "Run one additional check.",
            "transcriptPath": "/synthetic/should-not-enter-context", "lastAssistantMessage": "UNSCOPED-TRANSCRIPT",
        })
        engine = AsyncMock()

        async def answer(context):
            frozen = prepare_context(context)
            evidence = [frozen["observations"][0]["id"]]
            notes = []
            if any(row["session_id"] == "alpha" for row in frozen["candidates"]):
                observation = next(row for row in frozen["observations"]
                                   if row["session_id"] == "alpha" and row["kind"] == "stored_brief")
                notes = [{"session_id": "alpha", "summary": "Alpha appears focused on checks.",
                          "evidence_ids": [observation["id"]]}]
            return validate_plan({"reply": "Alpha and Beta have recorded review work.",
                                  "operation": "answer", "target_session_id": None,
                                  "message_source": None, "evidence_ids": evidence,
                                  "work_notes": notes}, frozen)
        engine.turn.side_effect = answer
        m._supervisor = engine if enabled else None
        m.broadcast_interruption = AsyncMock()
        m._do_mute = AsyncMock()
        m._jev.ask.return_value = judgment()
        speech = SpeechEvidence(m, statuses=statuses, actual_text=actual_text)
        return m, speech, engine

    @staticmethod
    def spoken(speech):
        return " ".join(frame.text for frame in speech.frames)

    def assert_no_work(self, m):
        self.run.assert_not_awaited()
        self.assertIsNone(m.dialogue.last_action)
        m._brain.compose_message.assert_not_awaited()

    async def test_two_real_turns_use_same_engine_all_agents_and_actual_delivery_context(self):
        m, speech, engine = self.fixture()
        await m._turn("Compare the work across the fleet.", None, None)
        await m._turn("And which part still needs attention?", None, None)
        self.assertEqual(engine.turn.await_count, 2)
        contexts = [call.args[0] for call in engine.turn.await_args_list]
        for context in contexts:
            self.assertEqual({row["session_id"] for row in context["candidates"]}, {"alpha", "beta"})
            self.assertEqual(context["stage"], "alpha")
            self.assertEqual(context["allowed_operations"], ["answer", "clarify"])
            self.assertEqual(context["message_sources"], [])
            self.assertNotIn("UNSCOPED-TRANSCRIPT", json.dumps(context))
            self.assertNotIn("transcriptPath", json.dumps(context))
        self.assertTrue(any(row["status"] == "output_complete" for row in contexts[1]["history"]))
        self.assertTrue(contexts[1]["inferred_notes"])
        self.assertEqual(contexts[1]["inferred_notes"][0]["kind"], "model_inference")
        self.assertEqual(len(speech.frames), 2)
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_no_work(m)

    async def test_supervisor_read_normalizes_receipt_judgment_to_spoken_summary(self):
        # Synthetic live routing selected the right read handler but called its
        # response a receipt. The adapter must still deliver the actual answer.
        m, speech, engine = self.fixture()
        m._jev.ask.return_value = judgment(response="receipt")
        await m._turn("Compare Alpha and Beta's recorded progress.", None, None)
        engine.turn.assert_awaited_once()
        self.assertEqual(speech.frames[-1].response_mode, "summary")
        self.assertEqual(speech.frames[-1].text, "Alpha and Beta have recorded review work.")
        self.assert_no_work(m)

    async def test_brief_cap_preserves_complete_current_candidate_inventory(self):
        m, _, engine = self.fixture()
        agents = [agent(f"worker-{i}", f"Worker {i}", 200 + i) for i in range(35)]
        m._tmux_fleet.return_value = parse_fleet(snapshot([pane(i + 1, [row]) for i, row in enumerate(agents)]))
        m._targets.return_value = [{"sessionId": row["sessionId"], "name": row["name"], "cwd": "/demo"} for row in agents]
        m.stage = m._targets.return_value[-1]
        await m._turn("Track every current agent.", None, None)
        context = engine.turn.call_args.args[0]
        self.assertEqual(len(context["candidates"]), 35)
        self.assertEqual(m._brief.await_count, 32)
        self.assertIn("worker-34", [call.args[0] for call in m._brief.await_args_list])
        self.assertEqual(context["observations"][0]["data"]["briefs_omitted_by_bound"], 3)
        self.assert_no_work(m)

    async def test_ack_and_stop_do_not_invoke_reasoning_or_dispatch(self):
        for kind in ("ack", "stop"):
            with self.subTest(kind=kind):
                m, speech, engine = self.fixture()
                m._jev.ask.return_value = (judgment(act="ack", route="none", response="silent")
                                           if kind == "ack" else protective_stop())
                await m._turn("mm-hmm" if kind == "ack" else "Stop speaking.", None, None)
                engine.turn.assert_not_awaited()
                self.assertEqual(speech.frames, [])
                if kind == "stop":
                    m.broadcast_interruption.assert_awaited_once()
                    m._do_mute.assert_awaited_once()
                self.assert_no_work(m)

    async def test_exact_directory_stays_literal_without_supervisor_or_answer_model(self):
        m, speech, engine = self.fixture()
        m._jev.ask.return_value = judgment(route="exact_directory", target="stage", response="exact_directory")
        await m._turn("Give me the full directory path.", None, None)
        self.assertEqual(speech.frames[-1].text, "/demo/alpha")
        engine.turn.assert_not_awaited()
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_no_work(m)

    async def test_direct_send_uses_original_source_and_persists_actual_cli_outcome(self):
        for code, expected in ((0, "sent"), (2, "not_sent"), (3, "waiting"), (5, "failed"), (124, "unknown")):
            with self.subTest(code=code):
                self.run.reset_mock()
                self.run.return_value = (code, "synthetic outcome")
                m, speech, engine = self.fixture()
                text = "Tell Alpha to review the indexing failure."
                m._jev.ask.return_value = judgment(act="direct", route="send_message", target="alpha",
                                                   response="receipt", execute=0.99)
                await m._turn(text, None, None)
                self.run.assert_awaited_once()
                self.assertEqual(self.run.call_args.args[1:], ("send", "alpha", text))
                engine.turn.assert_not_awaited()
                self.assertEqual(m.dialogue.last_action.status, expected)
                receipts = [row for row in m._supervisor_journal.context()["history"] if row["kind"] == "dispatch"]
                self.assertEqual([row["status"] for row in receipts], ["dispatching", expected])
                self.assertEqual({row["text"] for row in receipts}, {text})
                self.assertNotIn("completed", self.spoken(speech).lower())

    async def test_interrupted_response_is_generated_but_not_recorded_as_completed_output(self):
        m, _, _ = self.fixture(statuses=["interrupted", "interrupted"])
        await m._turn("Summarize the current work.", None, None)
        responses = [row for row in m._supervisor_journal.context()["history"] if row["kind"] == "response"]
        self.assertEqual([row["status"] for row in responses], ["generated", "interrupted_or_unknown"])
        self.assertNotIn("heard", json.dumps(responses))
        self.assert_no_work(m)

    async def test_completed_output_records_actual_spoken_text_not_full_generated_reply(self):
        m, _, _ = self.fixture(actual_text="Only the delivered portion.")
        await m._turn("Summarize all the details.", None, None)
        responses = [row for row in m._supervisor_journal.context()["history"] if row["kind"] == "response"]
        self.assertEqual(responses[0]["text"], "Alpha and Beta have recorded review work.")
        self.assertEqual(responses[0]["status"], "generated")
        self.assertEqual(responses[1]["text"], "Only the delivered portion.")
        self.assertEqual(responses[1]["status"], "output_complete")
        self.assert_no_work(m)

    async def test_provider_failure_speaks_truthful_receipt_and_records_failure(self):
        m, speech, engine = self.fixture()
        engine.turn.side_effect = SupervisorError("provider_failed", "synthetic diagnostic")
        await m._handle_turn("Assess the current work.", None, None)
        self.assertIn("couldn't finish", self.spoken(speech))
        self.assertIn("Nothing was sent", self.spoken(speech))
        self.assertTrue(any(row["kind"] == "failure" for row in m._supervisor_journal.context()["history"]))
        self.assert_no_work(m)

    async def test_journal_failure_before_reasoning_is_visible_and_does_not_launch_model(self):
        m, speech, engine = self.fixture()
        with patch.object(m._supervisor_journal, "event", side_effect=JournalError("synthetic storage failure")):
            await m._handle_turn("Assess the current work.", None, None)
        engine.turn.assert_not_awaited()
        self.assertTrue(speech.frames)
        self.assertRegex(self.spoken(speech).lower(), r"couldn't|cannot|can't|unavailable")
        self.assert_no_work(m)

    async def test_journal_failure_before_dispatch_records_not_sent_without_cli(self):
        m, speech, engine = self.fixture()
        m._jev.ask.return_value = judgment(act="direct", route="send_message", target="alpha",
                                           response="receipt", execute=0.99)
        with patch.object(m._supervisor_journal, "event", side_effect=JournalError("synthetic storage failure")):
            await m._handle_turn("Tell Alpha to check indexing.", None, None)
        self.run.assert_not_awaited()
        engine.turn.assert_not_awaited()
        self.assertEqual(m.dialogue.last_action.status, "not_sent")
        self.assertIn("not sent", self.spoken(speech).lower())

    async def test_post_send_journal_failure_does_not_hide_delivery_or_retry(self):
        m, speech, engine = self.fixture()
        m._jev.ask.return_value = judgment(act="direct", route="send_message", target="alpha",
                                           response="receipt", execute=0.99)
        original = m._supervisor_journal.event
        def record(kind, **fields):
            if kind == "dispatch" and fields.get("status") == "sent":
                raise JournalError("synthetic final receipt failure")
            return original(kind, **fields)
        with patch.object(m._supervisor_journal, "event", side_effect=record), patch("dialogue_manager.emit", AsyncMock()) as emitted:
            await m._handle_turn("Tell Alpha to check indexing.", None, None)
        self.run.assert_awaited_once()
        engine.turn.assert_not_awaited()
        self.assertEqual(m.dialogue.last_action.status, "sent")
        self.assertIn("Sent.", self.spoken(speech))
        all_events = [*emitted.await_args_list, *self.supervisor_events.await_args_list]
        self.assertTrue(any(call.kwargs.get("reason") == "receipt_persistence_failed" for call in all_events))

    async def test_late_supervisor_answer_after_accepted_stop_cannot_revive_or_enter_notes(self):
        m, speech, engine = self.fixture()
        started, release = asyncio.Event(), asyncio.Event()
        async def late(context):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return SupervisorPlan("This stale answer must not speak.", "answer", None, None, ())
        engine.turn.side_effect = late
        m._schedule_dialogue("Assess all agents.", None, None, "first")
        first = m._handler
        await started.wait()
        m._jev.ask.return_value = protective_stop()
        m._schedule_dialogue("Stop speaking.", None, None, "stop")
        await m._handler
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        self.assertEqual(speech.frames, [])
        self.assertFalse(any(row["kind"] == "response" for row in m._supervisor_journal.context()["history"]))
        self.assertEqual(m._supervisor_journal.context()["notes"], [])
        self.assert_no_work(m)

    async def test_ambiguous_current_identity_is_evidence_but_not_a_selectable_candidate(self):
        m, _, engine = self.fixture()
        m._tmux_fleet.return_value = parse_fleet(snapshot([
            pane(1, [agent("alpha", "Alpha", 101)]), pane(2, [agent("alpha", "Alpha", 102)]),
            pane(3, [agent("beta", "Beta", 103)])]))
        await m._turn("Which existing agents can be supervised?", None, None)
        context = engine.turn.call_args.args[0]
        self.assertEqual({row["session_id"] for row in context["candidates"]}, {"beta"})
        fleet = context["observations"][0]["data"]
        ambiguous = json.dumps({key: value for key, value in fleet.items() if "ambigu" in key})
        self.assertIn("alpha", ambiguous)
        self.assertIn("%1", ambiguous)
        self.assertIn("%2", ambiguous)
        self.assert_no_work(m)

    async def test_restart_reads_history_but_never_restores_old_confirmation_permission(self):
        directory = self.root / "restart"
        first, _, _ = self.fixture(directory=directory)
        pending = first.dialogue.prepare("Run indexing tests.", "alpha")
        first.dialogue.mark_offered(pending.pending_id)
        first._supervisor_journal.event("decision", request_id="old-proposal", target="alpha",
                                        text=pending.text, status="clarify")
        restarted, speech, engine = self.fixture(directory=directory)
        restarted._jev.ask.return_value = judgment(act="confirm", route="send_message", target="stage",
                                                   response="receipt", source="pending", execute=0.99)
        await restarted._turn("Yes, go ahead.", None, None)
        self.assertIsNone(restarted.dialogue.pending)
        engine.turn.assert_not_awaited()
        self.assertNotIn("Sent.", self.spoken(speech))
        self.assert_no_work(restarted)

    async def test_default_backend_keeps_existing_answer_provider_path(self):
        m, speech, engine = self.fixture(enabled=False)
        m._jev.ask.return_value = judgment(route="custom", target="stage")
        await m._turn("Explain the existing result.", None, None)
        m._brain.answer.assert_awaited_once()
        engine.turn.assert_not_awaited()
        self.assertIn("Two checks passed", self.spoken(speech))
        self.assert_no_work(m)

    async def test_large_existing_history_still_reaches_provider_through_valid_context(self):
        m, speech, engine = self.fixture()
        for index in range(55):
            m._supervisor_journal.event("response", request_id=f"old-{index}",
                                        text="Recorded response", status="generated")
        await m._turn("Bring me back to the work.", None, None)
        engine.turn.assert_awaited_once()
        self.assertLessEqual(len(engine.turn.call_args.args[0]["history"]), 40)
        self.assertIn("Alpha and Beta", self.spoken(speech))
        self.assert_no_work(m)

    async def test_cancel_before_audio_enqueue_does_not_attribute_previous_delivery_words(self):
        from types import SimpleNamespace
        m, _, _ = self.fixture()
        m._last_delivery = SimpleNamespace(generated_text="Previous agent's words.")
        m._say = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await m._turn("Assess current work.", None, None)
        responses = [row for row in m._supervisor_journal.context()["history"] if row["kind"] == "response"]
        self.assertTrue(any(row["status"] == "interrupted_or_unknown" for row in responses))
        self.assertFalse(any(row["text"] == "Previous agent's words." for row in responses))


if __name__ == "__main__":
    unittest.main()
