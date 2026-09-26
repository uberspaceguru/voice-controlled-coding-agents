"""Talking over Director: the verdicts, the strategy, and the open gate.

Text and synthetic PCM only. What these cannot show is acoustic: whether the
app's engine really removes Director's voice from the microphone. That is the
drill in research/barge-in-plan.md.
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.types import ProcessFrameResult

from barge_in import BargeInStrategy, classify, is_hold
from echo import EchoGate
from mute import EXTERNAL_UNTIL, WhileBotSpeaksMuteStrategy

LINE = "Director: No agents are waiting on you. The GPU one is ready to review."


class Verdicts(unittest.TestCase):
    def test_table(self):
        table = [
            ("mm-hm", "backchannel"), ("Mm hmm.", "backchannel"), ("yeah", "backchannel"),
            ("Okay.", "backchannel"), ("ok", "backchannel"), ("right right", "backchannel"),
            ("Got it.", "backchannel"), ("oh okay", "backchannel"), ("uh huh", "backchannel"),
            ("I see.", "backchannel"), ("yeah, makes sense", "backchannel"),
            ("Stop.", "stop"), ("wait", "stop"), ("No.", "stop"), ("hold on", "stop"),
            ("Hang on a second", "stop"), ("shut up", "stop"), ("never mind", "stop"),
            ("no no no", "stop"), ("Actually,", "stop"), ("Director", "stop"),
            ("okay stop", "stop"), ("yeah but wait", "stop"),
            ("what about the other one", "claim"), ("tell Yobi one to ship it", "claim"),
            ("I", "wait"), ("Seriously", "wait"), ("", "empty"), ("...", "empty"),
        ]
        for text, want in table:
            with self.subTest(text=text):
                self.assertEqual(classify(text), want)

    def test_names_cut_it_off(self):
        self.assertEqual(classify("Yobi1", extra_names=("Yobi1",)), "stop")
        self.assertEqual(classify("sys-3po", extra_names=("Sys-3PO",)), "stop")
        self.assertEqual(classify("ask sys-3po please", extra_names=("Sys-3PO",)), "stop")

    def test_its_own_voice_is_not_him(self):
        self.assertEqual(classify("the GPU one is ready", speaking_text=LINE), "echo")
        self.assertEqual(classify("no agents are", speaking_text=LINE), "echo")
        # One or two of its own words: hear one more before cutting it off.
        self.assertEqual(classify("Director", speaking_text=LINE), "wait")
        self.assertEqual(classify("No", speaking_text=LINE), "wait")
        # His words, even beside its own, are his.
        self.assertEqual(classify("stop", speaking_text=LINE), "stop")
        self.assertEqual(classify("no the other one", speaking_text=LINE), "stop")
        self.assertEqual(classify("what's the GPU one", speaking_text=LINE), "claim")

    def test_hold_is_short_and_a_stop(self):
        for text in ("Stop.", "wait", "hold on", "no", "Director", "no no no"):
            self.assertTrue(is_hold(text), text)
        for text in ("mm-hm", "no, the other one please", "what about the GPU one"):
            self.assertFalse(is_hold(text), text)


class Strategy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.verdicts = []
        self.s = BargeInStrategy(speaking_text=lambda: LINE, names=lambda: ("Yobi1",),
                                 on_verdict=lambda *a: self.verdicts.append(a))
        self.s.trigger_user_turn_started = AsyncMock()
        self.s.trigger_reset_aggregation = AsyncMock()

    async def say(self, text, final=False):
        cls = TranscriptionFrame if final else InterimTranscriptionFrame
        return await self.s.process_frame(cls(text=text, user_id="", timestamp=""))

    async def test_backchannels_never_interrupt(self):
        await self.s.process_frame(BotStartedSpeakingFrame())
        for text in ("mm", "mm-hm", "yeah", "okay", "got it"):
            self.assertEqual(await self.say(text), ProcessFrameResult.CONTINUE)
        self.assertEqual(await self.say("yeah okay", final=True), ProcessFrameResult.CONTINUE)
        self.s.trigger_user_turn_started.assert_not_awaited()
        self.s.trigger_reset_aggregation.assert_awaited()  # the final "yeah okay" is not kept

    async def test_one_stop_word_interrupts_at_once(self):
        await self.s.process_frame(BotStartedSpeakingFrame())
        await self.s.process_frame(VADUserStartedSpeakingFrame())
        self.assertEqual(await self.say("stop"), ProcessFrameResult.STOP)
        self.s.trigger_user_turn_started.assert_awaited_once()
        label, text, ms = self.verdicts[-1]
        self.assertEqual((label, text), ("stop", "stop"))
        self.assertIsNotNone(ms)

    async def test_a_claim_needs_two_words(self):
        await self.s.process_frame(BotStartedSpeakingFrame())
        self.assertEqual(await self.say("what"), ProcessFrameResult.CONTINUE)
        self.assertEqual(await self.say("what about"), ProcessFrameResult.STOP)

    async def test_its_own_voice_does_not_interrupt(self):
        await self.s.process_frame(BotStartedSpeakingFrame())
        self.assertEqual(await self.say("no agents are waiting"), ProcessFrameResult.CONTINUE)
        self.s.trigger_user_turn_started.assert_not_awaited()

    async def test_the_quiet_is_unchanged(self):
        await self.s.process_frame(BotStartedSpeakingFrame())
        await self.s.process_frame(BotStoppedSpeakingFrame())
        # One word starts a turn from silence, as MinWords(…) always did here;
        # "mm-hm" in the quiet is the gate's to judge, not this strategy's.
        self.assertEqual(await self.say("mm-hm"), ProcessFrameResult.STOP)


PCM = b"\x01\x04" * 1280


class OpenGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.addCleanup(patch.stopall)
        patch("echo.time", SimpleNamespace(monotonic=lambda: self.now)).start()
        patch("mute.time", SimpleNamespace(monotonic=lambda: self.now)).start()
        patch.object(FrameProcessor, "process_frame", AsyncMock()).start()
        old = EXTERNAL_UNTIL["t"]
        EXTERNAL_UNTIL["t"] = 0.0
        self.addCleanup(EXTERNAL_UNTIL.__setitem__, "t", old)

    async def test_gate_open_over_its_own_voice_but_shut_for_the_apps(self):
        gate = EchoGate(cancels_own_voice=True)
        gate.push_frame = AsyncMock()
        await gate.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        frame = InputAudioRawFrame(PCM, sample_rate=16000, num_channels=1)
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(frame.audio, PCM)
        # A right-hand's answer on its card is the app's voice, which the
        # engine does not cancel: that window still gates.
        EXTERNAL_UNTIL["t"] = self.now + 3
        frame = InputAudioRawFrame(PCM, sample_rate=16000, num_channels=1)
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(frame.audio, bytes(len(PCM)))

    async def test_mute_strategy_open_over_its_own_voice(self):
        closed, open_ = WhileBotSpeaksMuteStrategy(), WhileBotSpeaksMuteStrategy(cancels_own_voice=True)
        with patch("pipecat.turns.user_mute.base_user_mute_strategy.BaseUserMuteStrategy.process_frame",
                   AsyncMock(return_value=False)):
            self.assertTrue(await closed.process_frame(BotStartedSpeakingFrame()))
            self.assertFalse(await open_.process_frame(BotStartedSpeakingFrame()))
            EXTERNAL_UNTIL["t"] = self.now + 3
            self.assertTrue(await open_.process_frame(BotStartedSpeakingFrame()))

    async def test_default_gate_unchanged(self):
        gate = EchoGate()
        gate.push_frame = AsyncMock()
        await gate.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        frame = InputAudioRawFrame(PCM, sample_rate=16000, num_channels=1)
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.assertEqual(frame.audio, bytes(len(PCM)))


if __name__ == "__main__":
    unittest.main()


class OverTheVoice(unittest.IsolatedAsyncioTestCase):
    """The manager's half: a turn taken over Director's voice supersedes it."""

    async def asyncSetUp(self):
        import os
        from test_memory_manager import make_manager
        self.m = make_manager()
        self.m._say = AsyncMock(return_value=True)
        self.patches = [patch("manager.note"), patch("dialogue_manager.emit", AsyncMock()),
                        patch("manager.emit", AsyncMock()),
                        patch.dict(os.environ, {"TB_RIGHT_HAND_CARDS": "1",
                                                "TB_DEFAULT_INTERLOCUTOR": "director"})]
        for p in self.patches:
            p.start()
        self.m._card_secs = lambda reply: 0

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()

    async def test_wait_over_the_voice_stops_it_and_asks_nobody(self):
        run = AsyncMock(return_value=(0, "Done."))
        with patch("manager._run", run):
            epoch = self.m.dialogue.epoch
            self.m.barged_in()
            await self.m._dialogue_turn("wait", None, None)
        run.assert_not_awaited()
        self.assertNotEqual(self.m.dialogue.epoch, epoch, "what it was saying is superseded")
        self.assertGreater(self.m._follow_up_until, time.monotonic(), "the conversation stays open")

    async def test_a_claim_over_the_voice_supersedes_and_is_asked(self):
        run = AsyncMock(return_value=(0, "The other one is the GPU run."))
        with patch("manager._run", run):
            await self.m._dialogue_turn("Director, what needs me?", None, None)
            epoch = self.m.dialogue.epoch
            self.m.barged_in()
            await self.m._dialogue_turn("no the other one", None, None)
        self.assertEqual(run.await_args_list[-1].args[2], "no the other one")
        self.assertNotEqual(self.m.dialogue.epoch, epoch)

    async def test_the_same_words_in_the_quiet_change_nothing(self):
        run = AsyncMock(return_value=(0, "Done."))
        with patch("manager._run", run):
            await self.m._dialogue_turn("Director, what needs me?", None, None)
            epoch = self.m.dialogue.epoch
            await self.m._dialogue_turn("the second one", None, None)   # a follow-up, not over the voice
        self.assertEqual(self.m.dialogue.epoch, epoch)
