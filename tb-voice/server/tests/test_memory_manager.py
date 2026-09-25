"""Real handler/memory/speech-boundary tests with synthetic provider/output evidence.

No provider, device, native app, fleet command, or real conversation is accessed.
The normal _say path runs against correlated generated/output accounting; these
checks establish handler behavior, not audible playback or human comprehension.
"""

import asyncio
import unittest
from collections import deque
from contextlib import ExitStack
from types import MethodType
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import TTSAudioRawFrame

from conversation_memory import Memory
from dialogue import Decision, Record
from exact_speech import ExactSpeakFrame
from manager import Manager

TARGETS = [
    {"sessionId": "alpha", "name": "Alpha", "cwd": "/demo/alpha", "goal": "Review the API checks."},
    {"sessionId": "beta", "name": "Beta", "cwd": "/demo/beta", "goal": "Review the browser checks."},
]


def judgment(response="exact_directory", route="exact_directory"):
    def choice(value):
        return {"choice": value, "probabilities": {value: 0.99}, "confidence": 0.99}
    return {
        "addressed": {"noul": 0.99}, "execute": {"noul": 0.01},
        "act": choice("inform"), "target": choice("stage"), "source": choice("utterance"),
        "response": choice(response), "route": choice(route),
    }


def decision(response="exact_directory", route="exact_directory", text="Read the directory.", target="alpha"):
    return Decision("answer", "information", response=response, route=route, text=text, target=target)


def make_manager():
    m = object.__new__(Manager)
    m._init_dialogue()
    m.memory = Memory()
    m.stage = TARGETS[0].copy()
    m.dialogue.sync("alpha", TARGETS)
    m._targets = AsyncMock(return_value=TARGETS)
    # No Director on a test machine: the fleet falls back to this process's own
    # list, which is the path these tests pin (director_link has its own tests).
    m._director_inventory = AsyncMock(return_value=None)
    m._brief = AsyncMock(return_value={"sessionId": "alpha", "eventId": "one", "lastAssistantMessage": "Command: `git status`"})
    m._jev = AsyncMock()
    m._jev.ask.return_value = judgment()
    m._brain = AsyncMock()
    m._brain.answer.return_value = "Two checks passed."
    m._earcon = AsyncMock()
    m._recent = []
    m._held = None
    m._held_task = None
    m._voice = asyncio.Lock()
    m._bot_stopped = asyncio.Event()
    m.heard = m.addressed = 0
    m._say = MethodType(Manager._say, m)
    m.deliverybook.on_change = lambda _: None
    return m


class SpeechEvidence:
    def __init__(self, manager, statuses=(), actual_text=None, before_output=None):
        self.manager = manager
        self.statuses = deque(statuses)
        self.actual_text = actual_text
        self.before_output = before_output
        self.frames = []
        manager.push_frame = AsyncMock(side_effect=self.push)

    async def push(self, frame, *args):
        self.frames.append(frame)
        book = self.manager.deliverybook
        context = "synthetic-context-" + str(len(self.frames))
        text = self.actual_text if self.actual_text is not None else frame.text
        book.bind(frame.delivery, context, text)
        generated = TTSAudioRawFrame(audio=b"\0" * 1920, sample_rate=48000, num_channels=1, context_id=context)
        output = TTSAudioRawFrame(audio=b"\0" * 1920, sample_rate=48000, num_channels=1, context_id=context)
        book.generated_audio(context, generated)
        book.provider_end(context)
        book.generation_complete(context)
        if self.before_output:
            await self.before_output(frame)
        status = self.statuses.popleft() if self.statuses else "output_complete"
        if status != "output_complete":
            book.finish(frame.delivery, status, "synthetic_" + status)
        else:
            book.output_start(context)
            book.output_audio(context, output)
            book.output_stop(context)


class MemoryHandlers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        for module in ("manager", "dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.run = self.patches.enter_context(patch("tools._run", AsyncMock(return_value=(0, ""))))
        self.native_run = self.patches.enter_context(patch("manager._run", AsyncMock(return_value=(0, ""))))

    def tearDown(self):
        self.run.assert_not_awaited()
        self.native_run.assert_not_awaited()
        self.patches.close()

    async def test_exact_interruption_retries_once_then_resolves_same_question_from_output(self):
        m = make_manager()
        speech = SpeechEvidence(m, ("interrupted", "output_complete"))
        await m._turn("What directory are you working in?", None, None)
        self.assertEqual(len(speech.frames), 2)
        self.assertTrue(all(isinstance(frame, ExactSpeakFrame) for frame in speech.frames))
        questions = list(m.memory._questions.values())
        self.assertEqual(len(questions), 1)
        q = questions[0]
        self.assertEqual((q.status, q.repair_count, q.answer_text), ("resolved", 1, "/demo/alpha"))
        self.assertEqual(q.source_id, "targets:alpha")
        self.assertEqual(q.delivery_id, speech.frames[-1].delivery.id)
        self.assertEqual([frame.delivery.status for frame in speech.frames], ["interrupted", "output_complete"])
        m._brain.answer.assert_not_awaited()

    async def test_twice_interrupted_exact_answer_stays_open_without_multiplying_retries(self):
        m = make_manager()
        speech = SpeechEvidence(m, ("interrupted", "interrupted"))
        await m._dialogue_answer(decision(), None, None)
        q = m.memory.unresolved("alpha")
        self.assertIsNotNone(q)
        self.assertEqual((q.repair_count, q.delivery_status), (1, "interrupted"))
        self.assertEqual(len(speech.frames), 2)
        self.assertIsNone(q.resolved_at)

    async def test_true_return_or_generated_only_evidence_cannot_close_exact_question(self):
        for generated in (False, True):
            with self.subTest(generated=generated):
                m = make_manager()
                async def fake_say(text, **kwargs):
                    if generated:
                        delivery = m.deliverybook.create(text)
                        m._last_delivery = delivery
                        m.deliverybook.bind(delivery, "generated-only", text)
                        audio = TTSAudioRawFrame(audio=b"\0" * 1920, sample_rate=48000, num_channels=1)
                        m.deliverybook.generated_audio("generated-only", audio)
                        m.deliverybook.provider_end("generated-only")
                        m.deliverybook.generation_complete("generated-only")
                    return True
                m._say = fake_say
                await m._dialogue_answer(decision(), None, None)
                q = m.memory.unresolved("alpha")
                self.assertIsNotNone(q)
                self.assertNotEqual(q.delivery_status, "output_complete")
                self.assertIsNone(q.resolved_at)
                self.assertEqual(q.repair_count, 0)

    async def test_completed_sanitized_substitute_does_not_resolve_literal_question(self):
        m = make_manager()
        speech = SpeechEvidence(m, actual_text="a file path")
        await m._dialogue_answer(decision(), None, None)
        q = m.memory.unresolved("alpha")
        self.assertIsNotNone(q)
        self.assertEqual((q.answer_text, q.repair_count), ("a file path", 1))
        self.assertEqual(len(speech.frames), 2)
        self.assertTrue(all(frame.delivery.status == "output_complete" for frame in speech.frames))
        self.assertIsNone(q.resolved_at)

    async def test_command_prefix_completion_is_not_literal_value_completion(self):
        m = make_manager()
        observed_prefix = []
        async def before_output(frame):
            if frame.text == "Last reported command:":
                observed_prefix.append(m.memory.unresolved("alpha"))
        speech = SpeechEvidence(m, ("output_complete", "unknown"), before_output=before_output)
        await m._dialogue_answer(decision("exact_command", "exact_command", "Read the exact command."), None, None)
        self.assertEqual(len(observed_prefix), 1)
        self.assertEqual(len(speech.frames), 2)
        q = m.memory.unresolved("alpha")
        self.assertIs(q, observed_prefix[0])
        self.assertEqual(q.kind, "command")
        self.assertIsNone(q.resolved_at)
        self.assertIsNone(m.dialogue.last_command)

    async def test_generic_completed_answer_is_unverified_and_does_not_close_an_exact_question(self):
        m = make_manager()
        exact = m.memory.ask("The whole path, please.", "alpha", "directory")
        SpeechEvidence(m)
        await m._dialogue_answer(decision("detail", "custom", "Explain the latest result."), None, None)
        generic = m.memory.unresolved("alpha")
        self.assertEqual((generic.kind, generic.status), ("information", "relevance_unverified"))
        self.assertEqual(exact.status, "open")
        self.assertIsNone(exact.resolved_at)
        self.assertIn("exact directory question", m.memory.resume("alpha", "Alpha"))

    async def test_wrong_session_brief_receipt_cannot_close_exact_fact(self):
        m = make_manager()
        m._brief.return_value = {"sessionId": "beta", "lastAssistantMessage": "Branch: `feature/other`"}
        SpeechEvidence(m)
        await m._dialogue_answer(decision("exact_branch", "exact_branch", "Read the branch name."), None, None)
        q = m.memory.unresolved("alpha")
        self.assertEqual((q.kind, q.status), ("branch", "open"))
        self.assertIsNone(q.resolved_at)
        observations = list(m.memory._observations.values())
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].payload, {"kind": "branch", "available": False})
        self.assertEqual(q.repair_count, 1)
        m._brain.answer.assert_not_awaited()

    async def test_repeated_unchanged_update_is_suppressed_before_answer_generation(self):
        m = make_manager()
        m._brief.return_value = {"sessionId": "alpha", "eventId": "read-one", "recap": "Two checks passed."}
        speech = SpeechEvidence(m)
        request = decision("summary", "rung_findings", "What changed?")
        await m._dialogue_answer(request, None, None)
        m._brief.return_value = {"sessionId": "alpha", "eventId": "read-two", "recap": "Two checks passed."}
        await m._dialogue_answer(request, None, None)
        self.assertEqual(m._brain.answer.await_count, 1)
        self.assertEqual([frame.text for frame in speech.frames], ["Two checks passed.", "No new recorded update."])

    async def test_interrupted_update_is_not_baseline_and_changed_payload_survives_same_words(self):
        m = make_manager()
        m._brief.return_value = {"sessionId": "alpha", "recap": "Two checks passed."}
        speech = SpeechEvidence(m, ("interrupted", "interrupted", "output_complete", "output_complete"))
        request = decision("summary", "rung_findings", "What changed?")
        await m._dialogue_answer(request, None, None)
        self.assertEqual(len(m.memory._baselines), 0)
        await m._dialogue_answer(request, None, None)
        self.assertEqual(m._brain.answer.await_count, 2)
        m._brief.return_value = {"sessionId": "alpha", "recap": "Three checks passed."}
        await m._dialogue_answer(request, None, None)
        self.assertEqual(m._brain.answer.await_count, 3)
        self.assertEqual(len(speech.frames), 4)
        self.assertEqual({frame.text for frame in speech.frames}, {"Two checks passed."})

    async def test_critical_failure_or_pending_decision_repeats_bypass_suppression(self):
        for extra in ({"recap": "A check failed."}, {"recap": "Checks passed.", "proposal": "Choose which suite to run."}):
            with self.subTest(critical=extra):
                m = make_manager()
                m._brief.return_value = {"sessionId": "alpha", **extra}
                speech = SpeechEvidence(m)
                request = decision("summary", "rung_findings", "What changed?")
                await m._dialogue_answer(request, None, None)
                await m._dialogue_answer(request, None, None)
                self.assertEqual(m._brain.answer.await_count, 2)
                self.assertEqual(len(speech.frames), 2)
                self.assertIsNone(m.dialogue.proposal)

    async def test_explicit_repeat_and_repeated_exact_requests_always_answer(self):
        m = make_manager()
        m._brief.return_value = {"sessionId": "alpha", "recap": "Checks passed."}
        speech = SpeechEvidence(m)
        await m._dialogue_answer(decision("summary", "rung_findings", "What changed?"), None, None)
        await m._dialogue_answer(decision("summary", "rung_findings", "Read that again."), None, None)
        self.assertEqual(m._brain.answer.await_count, 2)
        await m._dialogue_answer(decision(), None, None)
        await m._dialogue_answer(decision(text="Give me that path again."), None, None)
        self.assertEqual([frame.text for frame in speech.frames[-2:]], ["/demo/alpha", "/demo/alpha"])
        exact_questions = [q for q in m.memory._questions.values() if q.kind == "directory"]
        self.assertEqual([q.status for q in exact_questions], ["resolved", "resolved"])

    async def test_undelivered_solution_does_not_create_a_confirmable_proposal(self):
        for legacy_true in (False, True):
            with self.subTest(legacy_true=legacy_true):
                m = make_manager()
                m._brief.return_value = {
                    "sessionId": "alpha", "proposal": "Run targeted checks.",
                    "rungs": [{"kind": "solution", "spoken": "The next step is targeted checks."}],
                }
                if legacy_true:
                    async def unsupported_true(*args, **kwargs):
                        return True
                    m._say = unsupported_true
                else:
                    SpeechEvidence(m, ("unknown",))
                await m._dialogue_answer(decision("summary", "rung_solution", "What's the next step?"), None, None)
                self.assertIsNone(m.dialogue.proposal)
                self.assertIsNotNone(m.memory.unresolved("alpha"))

    async def test_resumption_reports_current_agent_unresolved_pending_and_sent_without_actions(self):
        m = make_manager()
        m.memory.ask("Read the directory.", "alpha", "directory")
        m.memory.ask("Read the identifier.", "beta", "identifier")
        now = m.dialogue.clock()
        m.dialogue.prepare("Run the API checks.", "alpha", status="held")
        m.dialogue.last_action = Record("Inspect the API result.", "alpha", now, "sent")
        speech = SpeechEvidence(m)
        question_count = len(m.memory._questions)
        await m._dialogue_answer(decision("summary", "conversation_resume", "Where were we?"), None, None)
        self.assertEqual(len(speech.frames), 1)
        text = speech.frames[0].text
        self.assertIn("Back with Alpha", text)
        self.assertIn("exact directory question", text)
        self.assertIn("on hold", text)
        self.assertIn("sent; completion is not confirmed", text)
        self.assertIn("Review the API checks.", text)
        self.assertNotIn("Beta", text)
        self.assertNotIn("session identifier", text)
        self.assertEqual(len(m.memory._questions), question_count)
        m._brain.answer.assert_not_awaited()

    async def test_stage_switch_during_output_cannot_resolve_either_agent_question(self):
        m = make_manager()
        beta = m.memory.ask("Read Beta's directory.", "beta", "directory")
        async def stage_switch(frame):
            # An external stage change must not retarget this turn's guard.
            m.stage = TARGETS[1].copy()
            m.dialogue.sync("beta", TARGETS)
        speech = SpeechEvidence(m, before_output=stage_switch)
        with self.assertRaises(asyncio.CancelledError):
            await m._turn("What directory are you working in?", None, None)
        alpha = m.memory.unresolved("alpha")
        self.assertIsNotNone(alpha)
        self.assertIs(m.memory.unresolved("beta"), beta)
        self.assertIsNone(alpha.resolved_at)
        self.assertIsNone(beta.resolved_at)
        self.assertEqual(speech.frames[0].delivery.status, "interrupted")
        self.assertEqual(len(m.memory._baselines), 0)

    async def test_late_exact_output_cannot_close_replacement_question_or_reset_its_budget(self):
        m = make_manager()
        replacement = []
        async def replace_question(frame):
            if not replacement:
                replacement.append(m._memory_question(decision(text="Use the entire path this time.")))
        speech = SpeechEvidence(m, before_output=replace_question)
        await m._dialogue_answer(decision(), None, None)
        questions = list(m.memory._questions.values())
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0].status, "superseded")
        self.assertIs(m.memory.unresolved("alpha"), replacement[0])
        self.assertEqual(replacement[0].repair_count, 0)
        self.assertIsNone(replacement[0].resolved_at)
        self.assertEqual(len(speech.frames), 1)

    async def test_cancel_unanswered_question_does_not_resurface_on_resumption(self):
        m = make_manager()
        speech = SpeechEvidence(m, ("interrupted", "interrupted"))
        await m._turn("What directory are you working in?", None, None)
        question = m.memory.unresolved("alpha")
        self.assertIsNotNone(question)
        cancel = judgment(response="receipt", route="none")
        cancel["act"] = {"choice": "cancel", "probabilities": {"cancel": 0.99}, "confidence": 0.99}
        m._jev.ask.return_value = cancel
        await m._turn("Forget the unanswered question.", None, None)
        self.assertEqual(question.status, "canceled")
        self.assertIsNotNone(question.cancellation_source_id)
        self.assertIn("Canceled the unanswered question", speech.frames[-1].text)
        self.assertIsNone(m.memory.unresolved("alpha"))
        await m._dialogue_answer(decision("summary", "conversation_resume", "Where were we?"), None, None)
        self.assertNotIn("unanswered", speech.frames[-1].text)
        self.assertNotIn("needs a verified answer", speech.frames[-1].text)
        self.assertIsNone(m.dialogue.pending)
        self.assertIsNone(m.dialogue.last_action)

    async def test_cancel_unsent_work_does_not_cancel_an_unrelated_unanswered_question(self):
        m = make_manager()
        SpeechEvidence(m)
        question = m.memory.ask("Read the working directory.", "alpha", "directory")
        m.dialogue.prepare("Run the API checks.", "alpha")
        cancel = judgment(response="receipt", route="none")
        cancel["act"] = {"choice": "cancel", "probabilities": {"cancel": 0.99}, "confidence": 0.99}
        m._jev.ask.return_value = cancel
        await m._turn("Cancel the unsent request.", None, None)
        self.assertIsNone(m.dialogue.pending)
        self.assertIs(m.memory.unresolved("alpha"), question)
        self.assertEqual(question.status, "open")
        self.assertIsNone(question.cancellation_source_id)

    async def test_cancel_information_after_sent_work_preserves_action_and_second_cancel_is_truthful(self):
        m = make_manager()
        speech = SpeechEvidence(m)
        action = Record("Run the API checks.", "alpha", m.dialogue.clock(), "sent")
        m.dialogue.last_action = action
        m.dialogue.last_information = "summary"
        question = m.memory.ask("What caused the latest warning?", "alpha", "information")
        cancel = judgment(response="receipt", route="none")
        cancel["act"] = {"choice": "cancel", "probabilities": {"cancel": 0.99}, "confidence": 0.99}
        m._jev.ask.return_value = cancel
        await m._turn("Cancel that unanswered question.", None, None)
        self.assertEqual(question.status, "canceled")
        self.assertIsNone(m.memory.unresolved("alpha"))
        self.assertIsNone(m.dialogue.last_information)
        self.assertIs(m.dialogue.last_action, action)
        self.assertEqual(action.status, "sent")
        self.assertIn("Canceled the unanswered question", speech.frames[-1].text)
        self.assertIn("unchanged", speech.frames[-1].text)
        await m._turn("Never mind.", None, None)
        self.assertIn("cannot undo", speech.frames[-1].text.lower())
        self.assertNotIn("Canceled the unanswered question", speech.frames[-1].text)
        self.assertIs(m.dialogue.last_action, action)
        self.assertEqual(action.status, "sent")
        self.assertIsNone(m.dialogue.pending)


if __name__ == "__main__":
    unittest.main()
