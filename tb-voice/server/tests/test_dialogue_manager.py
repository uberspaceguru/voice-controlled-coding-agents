import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.gradium.tts import GradiumTTSService

from exact_speech import DialogueSpeakFrame
from manager import Manager
from tts import SpokenGradiumTTSService

TARGETS = [{"sessionId": "alpha", "name": "Alpha", "cwd": "/demo/alpha"},
           {"sessionId": "beta", "name": "Beta", "cwd": "/demo/beta"}]


def judgment(act="direct", target="stage", source="utterance", response="receipt",
             route="send_message", execute=0.99):
    def c(value):
        return {"choice": value, "probabilities": {value: 0.99}, "confidence": 0.99}
    return {"addressed": {"noul": 0.99}, "act": c(act), "target": c(target),
            "source": c(source), "response": c(response), "route": c(route),
            "execute": {"noul": execute}}


def manager():
    m = object.__new__(Manager)
    m._init_dialogue()
    m.stage = TARGETS[0].copy()
    m._targets = AsyncMock(return_value=TARGETS)
    m._brief = AsyncMock(return_value={"sessionId": "alpha", "lastAssistantMessage": "Command: `git status`"})
    m._jev = AsyncMock()
    m._jev.ask.return_value = judgment()
    m._brain = AsyncMock()
    m._brain.answer.return_value = "The tests passed."
    m._say = AsyncMock(return_value=True)
    m._earcon = AsyncMock()
    m._recent = []
    m._held = None
    m._held_task = None
    m._voice = asyncio.Lock()
    m._bot_stopped = asyncio.Event()
    m.heard = m.addressed = 0
    m.push_frame = AsyncMock()
    return m


class Integration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        for module in ("manager", "dialogue_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.run = self.patches.enter_context(patch("tools._run", AsyncMock(return_value=(0, ""))))

    def tearDown(self):
        self.patches.close()

    async def test_direct_source_is_sent_once_without_composition(self):
        m = manager()
        await m._turn("Run the tests.", None, None)
        self.run.assert_awaited_once()
        self.assertEqual(self.run.call_args.args[1:], ("send", "alpha", "Run the tests."))
        m._brain.compose_message.assert_not_awaited()
        self.assertEqual(m.dialogue.last_action.status, "sent")
        self.assertEqual(m._say.call_args.args[0], "Sent.")

    async def test_actual_delivery_receipts(self):
        for code, status, phrase in [(2, "not_sent", "not sent"), (3, "waiting", "waiting"),
                                     (5, "failed", "failed"), (124, "unknown", "unknown")]:
            m = manager()
            self.run.return_value = (code, "")
            await m._turn("Run the tests.", None, None)
            self.assertEqual(m.dialogue.last_action.status, status)
            self.assertIn(phrase, m._say.call_args.args[0])

    async def test_slow_judgment_cannot_send_after_cancel_turn(self):
        m = manager()
        started, release = asyncio.Event(), asyncio.Event()
        async def ask(*args):
            started.set()
            await release.wait()
            return judgment()
        m._jev.ask.side_effect = ask
        m._schedule_dialogue("Run tests.", None, None, "user:1")
        old = m._handler
        await started.wait()
        m._jev.ask.side_effect = None
        m._jev.ask.return_value = judgment("cancel", route="none", execute=0)
        m._schedule_dialogue("Never mind.", None, None, "user:2")
        release.set()
        await asyncio.gather(old, m._handler, return_exceptions=True)
        self.run.assert_not_awaited()

    async def test_stage_switch_during_read_prevents_dispatch(self):
        m = manager()
        async def ask(*args):
            m.stage = TARGETS[1].copy()
            return judgment()
        m._jev.ask.side_effect = ask
        with self.assertRaises(asyncio.CancelledError):
            await m._turn("Run tests.", None, None)
        self.run.assert_not_awaited()
        m._say.assert_not_awaited()

    async def test_pending_is_consumed_before_send_and_rejection_cannot_undo(self):
        m = manager()
        m.dialogue.sync("alpha", TARGETS)
        prepared = m.dialogue.prepare("git status", "alpha")
        m.dialogue.mark_offered(prepared.pending_id)
        started, finish = asyncio.Event(), asyncio.Event()
        async def send(*args, **kwargs):
            started.set()
            await finish.wait()
            return 0, ""
        self.run.side_effect = send
        m._jev.ask.return_value = judgment("confirm", source="pending")
        m._schedule_dialogue("Yes.", None, None, "one")
        original = m._handler
        await started.wait()
        self.assertIsNone(m.dialogue.pending)
        m._jev.ask.return_value = judgment("cancel", route="none", execute=0)
        m._schedule_dialogue("Never mind.", None, None, "two")
        await m._handler
        self.assertIn("cannot undo", m._say.call_args.args[0])
        finish.set()
        await asyncio.gather(original, *tuple(m._deliveries), return_exceptions=True)
        self.assertEqual(m.dialogue.last_action.status, "sent")
        self.assertEqual(self.run.await_count, 1)
        self.assertNotEqual(m._say.call_args.args[0], "Sent.")

    async def test_canceled_model_answer_is_never_spoken(self):
        m = manager()
        started, finish = asyncio.Event(), asyncio.Event()
        async def answer(*args, **kwargs):
            started.set()
            await finish.wait()
            return "Stale answer."
        m._brain.answer.side_effect = answer
        m._jev.ask.return_value = judgment("inform", response="detail", route="custom", execute=0)
        m._schedule_dialogue("Explain it.", None, None, "one")
        original = m._handler
        await started.wait()
        m._jev.ask.return_value = judgment("cancel", route="none", execute=0)
        m._schedule_dialogue("Never mind.", None, None, "two")
        finish.set()
        await asyncio.gather(original, m._handler, return_exceptions=True)
        self.assertNotIn("Stale answer.", [call.args[0] for call in m._say.call_args_list])
        self.run.assert_not_awaited()

    async def test_pipeline_backchannel_and_side_speech_preserve_ongoing_answer(self):
        for act, text in [("ack", "Mm-hmm."), ("think", "Jamie, the pizza is here.")]:
            m = manager()
            started, finish = asyncio.Event(), asyncio.Event()
            async def answer(*args, **kwargs):
                started.set()
                await finish.wait()
                return "The explanation is still current."
            m._brain.answer.side_effect = answer
            m._jev.ask.return_value = judgment("inform", response="detail", route="custom", execute=0)
            ctx = LLMContext([{"role": "user", "content": "Explain that result."}])
            with patch.object(FrameProcessor, "process_frame", AsyncMock()):
                await m.process_frame(LLMContextFrame(ctx), FrameDirection.DOWNSTREAM)
                original = m._handler
                await started.wait()
                epoch = m.dialogue.epoch
                await m.hearing()
                self.assertFalse(original.cancelled())
                m._jev.ask.return_value = judgment(act, response="silent", route="none", execute=0)
                ctx.add_message({"role": "user", "content": text})
                await m.process_frame(LLMContextFrame(ctx), FrameDirection.DOWNSTREAM)
                await m._handler
                self.assertEqual(m.dialogue.epoch, epoch)
                finish.set()
                await original
            self.assertEqual(m._say.call_args.args[0], "The explanation is still current.")
            self.run.assert_not_awaited()

    async def test_timed_out_solution_does_not_offer_a_proposal(self):
        m = manager()
        m._brief.return_value = {
            "sessionId": "alpha", "proposal": "Run the tests.",
            "rungs": [{"kind": "solution", "spoken": "Run the tests next."}],
        }
        m._say.return_value = False
        m._jev.ask.return_value = judgment("inform", response="summary", route="rung_solution", execute=0)
        await m._turn("What's the next step?", None, None)
        self.assertIsNone(m.dialogue.proposal)

    async def test_upstream_replay_and_duplicate_transcript(self):
        m = manager()
        context = LLMContext([{"role": "user", "content": "Run tests."}])
        with patch.object(FrameProcessor, "process_frame", AsyncMock()):
            await m.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
            await m._handler
            await m.process_frame(LLMContextFrame(context), FrameDirection.UPSTREAM)
            await m.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
            self.assertEqual(self.run.await_count, 1)
            context.add_message({"role": "user", "content": "Run tests."})
            await m.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
            await m._handler
            self.assertEqual(self.run.await_count, 2)

    async def test_failed_cancel_judgment_cannot_release_an_old_send(self):
        m = manager()
        reading, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def targets():
            nonlocal calls
            calls += 1
            if calls == 2:
                reading.set()
                await release.wait()
            return TARGETS
        m._targets.side_effect = targets
        m._schedule_dialogue("Run tests.", None, None, "one")
        old = m._handler
        await reading.wait()
        m._jev.ask.side_effect = RuntimeError("synthetic unavailable")
        m._schedule_dialogue("Never mind.", None, None, "two")
        await m._handler
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        self.run.assert_not_awaited()
        self.assertIsNone(m.dialogue.last_action)

    async def test_duplicate_final_releases_hearing_pause(self):
        m = manager()
        context = LLMContext([{"role": "user", "content": "Run tests."}])
        with patch.object(FrameProcessor, "process_frame", AsyncMock()):
            await m.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
            await m._handler
            await m.hearing()
            self.assertFalse(m._input_ready.is_set())
            await m.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
            self.assertTrue(m._input_ready.is_set())
        self.assertEqual(self.run.await_count, 1)

    async def test_unknown_delivery_does_not_offer_or_record_command(self):
        m = manager()
        m.dialogue.sync("alpha", TARGETS)
        decision = m.dialogue.prepare("git status", "alpha")
        m._say.return_value = None
        await m._execute_decision(decision, None, None)
        self.assertFalse(m.dialogue.pending.offered)
        await m._exact_value("command", "alpha")
        self.assertIsNone(m.dialogue.last_command)

    async def test_real_say_ignores_generic_stops_and_waits_own_delivery(self):
        from pipecat.frames.frames import BotStoppedSpeakingFrame
        m = manager()
        m.deliverybook.on_change = lambda _: None
        started = asyncio.Event()
        async def push(frame, *args):
            if isinstance(frame, DialogueSpeakFrame):
                started.set()
        m.push_frame.side_effect = push
        task = asyncio.create_task(Manager._say(m, "Current answer."))
        await started.wait()
        with patch.object(FrameProcessor, "process_frame", AsyncMock()):
            await m.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        self.assertFalse(task.done())
        m.deliverybook.finish(m._last_delivery, "failed", "synthetic_provider_failure")
        self.assertFalse(await task)

    async def test_real_say_retries_interrupted_output_at_most_once(self):
        m = manager()
        m.deliverybook.on_change = lambda _: None
        async def push(frame, *args):
            m.deliverybook.finish(frame.delivery, "interrupted", "synthetic_backchannel")
        m.push_frame.side_effect = push
        self.assertFalse(await Manager._say(m, "Current answer."))
        self.assertEqual(m.push_frame.await_count, 2)
        self.assertTrue(all(d.status == "interrupted" for d in m.deliverybook.records.values()))

    async def test_independent_rung_vote_cannot_overwrite_literal_response(self):
        m = manager()
        m._jev.ask.return_value = judgment("inform", response="exact_directory", route="rung_findings", execute=0)
        await m._turn("Actually, the full directory please.", None, None)
        self.assertEqual(m._say.call_args.args[0], "/demo/alpha")
        self.assertIsNotNone(m._say.call_args.kwargs["exact"])
        m._brain.answer.assert_not_awaited()
        self.run.assert_not_awaited()

    async def test_last_sent_summary_is_not_called_unsent(self):
        m = manager()
        await m._turn("Run tests.", None, None)
        m._jev.ask.return_value = judgment("inform", source="last_action", response="summary", route="custom", execute=0)
        await m._turn("What did you send?", None, None)
        self.assertEqual(m._say.call_args.args[0], "Sent request: Run tests.")
        self.assertEqual(self.run.await_count, 1)

    async def test_explicit_stop_delegates_existing_interruption(self):
        m = manager()
        m.broadcast_interruption = AsyncMock()
        m._do_mute = AsyncMock()
        m._jev.ask.return_value = judgment("control", route="stop_speaking", execute=0)
        await m._turn("Stop speaking.", None, None)
        m.broadcast_interruption.assert_awaited_once()
        m._do_mute.assert_awaited_once()
        self.run.assert_not_awaited()

    async def test_readonly_correction_reads_bound_unsent_payload(self):
        m = manager()
        m.dialogue.sync("alpha", TARGETS)
        m.dialogue.prepare("git diff --stat", "alpha")
        m._jev.ask.return_value = judgment("correct", response="exact_command", execute=0)
        await m._turn("Don't run it, just tell me.", None, None)
        self.assertEqual(m._say.call_args.args[0], "git diff --stat")
        self.assertIn("exact", m._say.call_args.kwargs)
        self.run.assert_not_awaited()
        m._brain.answer.assert_not_awaited()

    async def test_stale_speech_frame_drops_before_provider_and_detail_is_bounded(self):
        service = object.__new__(SpokenGradiumTTSService)
        heard = []
        async def synth(self, text, context):
            heard.append(text)
            if False:
                yield None
        async def process(self, frame, direction):
            async for _ in self.run_tts(frame.text, "ctx"):
                pass
        with patch.object(GradiumTTSService, "process_frame", process), patch.object(GradiumTTSService, "run_tts", synth):
            await service.process_frame(DialogueSpeakFrame(text="Stale.", current=lambda: False), FrameDirection.DOWNSTREAM)
            await service.process_frame(DialogueSpeakFrame(text="word " * 150, response_mode="detail"), FrameDirection.DOWNSTREAM)
        self.assertEqual(len(heard), 1)
        self.assertEqual(len(heard[0].split()), 120)


if __name__ == "__main__":
    unittest.main()
