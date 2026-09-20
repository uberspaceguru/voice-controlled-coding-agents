"""Protective stop-speech evidence must not become permission to dispatch work."""

import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from test_memory_manager import SpeechEvidence, make_manager


def choice(value, probability):
    return {"choice": value, "probabilities": {value: probability, "unknown": 1 - probability}, "confidence": 1.0}


def protective_stop():
    return {
        "act": choice("control", 0.77), "route": choice("stop_speaking", 0.95),
        "addressed": {"noul": 0.27}, "source": choice("utterance", 0.76),
        "target": choice("none", 0.83), "execute": {"noul": 0.07},
        "response": choice("silent", 0.95),
    }


def fixture():
    m = make_manager()
    m.broadcast_interruption = AsyncMock()
    m._do_mute = AsyncMock()
    speech = SpeechEvidence(m)
    return m, speech


class StopControl(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        for module in ("manager", "dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.run = self.patches.enter_context(patch("tools._run", AsyncMock(return_value=(0, ""))))
        self.native_run = self.patches.enter_context(patch("manager._run", AsyncMock(return_value=(0, ""))))

    def tearDown(self):
        self.native_run.assert_not_awaited()
        self.patches.close()

    def assert_no_execution(self, m):
        self.run.assert_not_awaited()
        self.assertIsNone(m.dialogue.last_action)
        m._brain.compose_message.assert_not_awaited()

    async def test_explicit_stop_with_low_addressedness_interrupts_without_speech_or_send(self):
        m, speech = fixture()
        m._jev.ask.return_value = protective_stop()
        await m._turn("Stop speaking.", None, None)
        m.broadcast_interruption.assert_awaited_once()
        m._do_mute.assert_awaited_once()
        self.assertEqual(speech.frames, [])
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_no_execution(m)

    async def test_quoted_or_side_speech_never_borrows_stop_control_permission(self):
        for text in ('The README says "stop speaking" after the demo.', 'Morgan, tell the speaker to stop talking.'):
            with self.subTest(text=text):
                m, speech = fixture()
                result = protective_stop()
                result["act"] = choice("think", 0.99)
                m._jev.ask.return_value = result
                await m._turn(text, None, None)
                m.broadcast_interruption.assert_not_awaited()
                m._do_mute.assert_not_awaited()
                self.assertEqual(speech.frames, [])
                self.assert_no_execution(m)

    async def test_protective_control_requires_independent_agreement(self):
        cases = (("act", "control", 0.60), ("route", "stop_speaking", 0.60),
                 ("source", "utterance", 0.40), ("target", "none", 0.40))
        for key, selected, probability in cases:
            with self.subTest(question=key):
                m, speech = fixture()
                result = protective_stop()
                result[key] = choice(selected, probability)
                m._jev.ask.return_value = result
                await m._turn("Stop speaking.", None, None)
                m.broadcast_interruption.assert_not_awaited()
                m._do_mute.assert_not_awaited()
                self.assertEqual(speech.frames, [])
                self.assert_no_execution(m)

    async def test_explicit_running_agent_stop_is_a_distinct_strict_dispatch(self):
        m, speech = fixture()
        result = protective_stop()
        result.update({
            "act": choice("control", 0.99), "route": choice("stop_agent", 0.99),
            "addressed": {"noul": 0.99}, "source": choice("utterance", 0.99),
            "target": choice("alpha", 0.99), "execute": {"noul": 0.99},
            "response": choice("receipt", 0.99),
        })
        m._jev.ask.return_value = result
        request = "Ask Alpha to stop its current task."
        await m._turn(request, None, None)
        self.run.assert_awaited_once()
        self.assertEqual(self.run.call_args.args[1:], ("send", "alpha", request))
        self.assertEqual(m.dialogue.last_action.status, "sent")
        self.assertIn("Stop request sent", speech.frames[-1].text)
        m.broadcast_interruption.assert_not_awaited()
        m._do_mute.assert_not_awaited()

    async def test_low_addressedness_protection_does_not_apply_to_stop_agent(self):
        m, speech = fixture()
        result = protective_stop()
        result["route"] = choice("stop_agent", 0.95)
        result["target"] = choice("alpha", 0.99)
        m._jev.ask.return_value = result
        await m._turn("Ask Alpha to stop its current task.", None, None)
        m.broadcast_interruption.assert_not_awaited()
        m._do_mute.assert_not_awaited()
        self.assertEqual(speech.frames, [])
        self.assert_no_execution(m)

    async def test_accepted_stop_prevents_a_late_old_answer_from_reviving(self):
        m, speech = fixture()
        started, release = asyncio.Event(), asyncio.Event()
        async def late_answer(*args, **kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return "A stale explanation that must never speak."
        m._brain.answer.side_effect = late_answer
        initial = protective_stop()
        initial.update({
            "act": choice("inform", 0.99), "route": choice("custom", 0.99),
            "addressed": {"noul": 0.99}, "source": choice("utterance", 0.99),
            "target": choice("stage", 0.99), "execute": {"noul": 0.01},
            "response": choice("detail", 0.99),
        })
        m._jev.ask.return_value = initial
        m._schedule_dialogue("Explain the current result.", None, None, "answer-1")
        old = m._handler
        await started.wait()
        m._jev.ask.return_value = protective_stop()
        m._schedule_dialogue("Stop speaking.", None, None, "stop-2")
        await m._handler
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        m.broadcast_interruption.assert_awaited_once()
        m._do_mute.assert_awaited_once()
        self.assertEqual(speech.frames, [])
        self.assert_no_execution(m)


if __name__ == "__main__":
    unittest.main()
