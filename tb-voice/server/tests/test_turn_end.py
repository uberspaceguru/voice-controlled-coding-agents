"""End of turn: the transcriber's forecast decides, words that hold the floor
wait 2.5 s, and silence alone ends a turn only after the fallback."""

import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import turn_end
from turn_end import (
    EndOfTurnSignal,
    ForecastGradiumSTTService,
    ForecastTurnStopStrategy,
    decide,
    holds_floor,
)
from words_tap import WordsTap


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def steps(*probs):
    """Step messages as the transcriber sends them: one entry per horizon."""
    return [[{"horizon_s": h, "inactivity_prob": (p if h == 2.0 else 0.0)} for h in (0.5, 1.0, 2.0, 3.0)]
            for p in probs]


class HoldsFloor(unittest.TestCase):
    def test_his_split_fragments_hold_the_floor(self):
        # Fragments from the 25 Sep session that were answered or dropped alone.
        for text, why in [("8264, to", "dangling"),
                          ("Something keeps saying, Director,", "cut_off"),
                          ("didn't... hear me right now so", "dangling"),
                          ("Director section so I can see what's.", "cut_off"),
                          ("I was going to ask you about the", "dangling"),
                          ("Tell the other agent to", "dangling"),
                          ("and then, um", "filler"),
                          ("Uh.", "filler"),
                          ("I think we should restart it because", "dangling"),
                          ("Check the logs and", "dangling"),
                          ("Is it one or", "dangling"),
                          ("Is it the GPU one or the —", "cut_off"),
                          ("the one with", "dangling"),
                          ("Send it to the branch of", "dangling"),
                          ("I want a", "dangling")]:
            self.assertEqual(holds_floor(text), why, text)

    def test_finished_sentences_do_not(self):
        for text in ("So what's on the docket?", "What do I need to know?", "Hey, director, can you hear me?",
                     "So that first one.", "I think so.", "Go with plan A", "Tell me more about that.",
                     "Stop!", "What is that for?", "", "   ", "Okay.", "8273."):
            self.assertIsNone(holds_floor(text), text)


class Decide(unittest.TestCase):
    def test_the_forecast_ends_a_finished_turn_at_a_short_pause(self):
        self.assertEqual(decide("So what's on the docket?", 0.4, (0.8, 2)), "semantic")
        self.assertIsNone(decide("So what's on the docket?", 0.4, (0.8, 1)), "two steps in a row")

    def test_holding_words_wait_out_the_forecast_until_two_and_a_half_seconds(self):
        self.assertIsNone(decide("8264, to", 0.6, (0.95, 9)))
        self.assertIsNone(decide("8264, to", 2.4, (0.95, 9)))
        self.assertEqual(decide("8264, to", 2.5, (0.95, 9)), "hold_dangling_timeout")

    def test_silence_alone_ends_a_turn_only_at_the_fallback(self):
        self.assertIsNone(decide("So what's on the docket?", 1.5, (0.2, 0)))
        self.assertEqual(decide("So what's on the docket?", 2.5, (0.2, 0)), "fallback_silence")

    def test_without_any_forecast_the_old_silence_rule_holds(self):
        self.assertIsNone(decide("So what's on the docket?", 1.0, (None, 0)))
        self.assertEqual(decide("So what's on the docket?", 1.2, (None, 0)), "silence_no_forecast")
        self.assertIsNone(decide("Tell the other agent to", 1.2, (None, 0)), "holding still waits")

    def test_no_words_never_end_a_turn(self):
        self.assertIsNone(decide("", 9.0, (0.99, 9)))


class Signal(unittest.TestCase):
    def test_reads_the_two_second_horizon_and_counts_steps_in_a_row(self):
        clock = Clock()
        sig = EndOfTurnSignal(horizon_s=2.0, threshold=0.5, clock=clock)
        for vad in steps(0.1, 0.6, 0.7):
            clock.now += 0.08
            sig.update(vad)
        self.assertEqual(sig.reading(), (0.7, 2))
        sig.update(steps(0.3)[0])
        self.assertEqual(sig.reading(), (0.3, 0))

    def test_unnamed_horizons_use_index_two(self):
        sig = EndOfTurnSignal(clock=Clock())
        sig.update([{"inactivity_prob": 0.1}, {"inactivity_prob": 0.2}, {"inactivity_prob": 0.9},
                    {"inactivity_prob": 0.3}])
        self.assertEqual(sig.prob, 0.9)

    def test_a_stale_or_earlier_forecast_is_no_forecast(self):
        clock = Clock()
        sig = EndOfTurnSignal(clock=clock)
        sig.update(steps(0.9)[0])
        self.assertEqual(sig.reading(since=clock.now + 0.1), (None, 0), "a step from before the pause")
        clock.now += 0.6
        self.assertEqual(sig.reading(), (None, 0), "no step for 0.6 s: a gap, not a verdict")


class Strategy(unittest.IsolatedAsyncioTestCase):
    def make(self):
        self.clock = Clock()
        self.signal = EndOfTurnSignal(clock=self.clock)
        s = ForecastTurnStopStrategy(self.signal, clock=self.clock)
        s._start_watch = lambda: None          # ticks are driven by the test
        s.trigger_user_turn_stopped = AsyncMock()
        return s

    async def pause(self, s, text, stop_secs=0.2):
        await s.process_frame(VADUserStartedSpeakingFrame())
        self.clock.now += 1.0
        await s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=stop_secs))
        await s.process_frame(TranscriptionFrame(text, "ahmed", "now"))

    async def advance(self, s, secs, prob=None):
        """Let `secs` pass in 80 ms steps, one forecast per step; the verdict of the last tick."""
        reason = None
        t = 0.0
        while t < secs - 1e-9 and not reason:
            self.clock.now += 0.08
            t += 0.08
            if prob is not None:
                self.signal.update(steps(prob)[0])
            reason = await s.evaluate()
        return reason, t

    async def test_a_finished_sentence_ends_on_the_forecast(self):
        s = self.make()
        await self.pause(s, "So what's on the docket?")
        reason, t = await self.advance(s, 3.0, prob=0.8)
        self.assertEqual(reason, "semantic")
        self.assertLess(t, 0.3)
        s.trigger_user_turn_stopped.assert_awaited_once()

    async def test_a_dangling_word_is_not_ended_by_a_short_silence(self):
        s = self.make()
        await self.pause(s, "8264, to")
        reason, t = await self.advance(s, 2.0, prob=0.95)
        self.assertIsNone(reason, "a confident forecast does not end 'to'")
        s.trigger_user_turn_stopped.assert_not_awaited()
        # He goes on: the rest joins the same turn.
        await s.process_frame(VADUserStartedSpeakingFrame())
        self.clock.now += 1.0
        await s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2))
        await s.process_frame(TranscriptionFrame("w-a27-3, please.", "ahmed", "now"))
        reason, _ = await self.advance(s, 1.0, prob=0.9)
        self.assertEqual(reason, "semantic")
        self.assertEqual(s._text, "8264, to w-a27-3, please.")

    async def test_a_dangling_word_ends_at_the_hold_limit(self):
        s = self.make()
        await self.pause(s, "and then, um")
        reason, t = await self.advance(s, 3.0, prob=0.95)
        self.assertEqual(reason, "hold_filler_timeout")
        self.assertGreaterEqual(t + 0.2, turn_end.HOLD_SECS - 1e-6, "silence counts from speech end")

    async def test_forecast_not_done_falls_back_to_two_and_a_half_seconds(self):
        s = self.make()
        await self.pause(s, "So what's on the docket?")
        reason, t = await self.advance(s, 3.0, prob=0.2)
        self.assertEqual(reason, "fallback_silence")
        self.assertGreaterEqual(t + 0.2, turn_end.FALLBACK_SECS - 1e-6)

    async def test_words_still_arriving_hold_the_turn(self):
        s = self.make()
        await self.pause(s, "So what's on the docket?")
        await s.process_frame(InterimTranscriptionFrame("What do", "ahmed", "now"))
        reason, _ = await self.advance(s, 1.0, prob=0.9)
        self.assertIsNone(reason, "an interim after the final: more text is on its way")

    async def test_speech_resuming_cancels_the_pending_end(self):
        s = self.make()
        await self.pause(s, "So what's on the docket?")
        await s.process_frame(VADUserStartedSpeakingFrame())
        reason, _ = await self.advance(s, 3.0, prob=0.9)
        self.assertIsNone(reason)

    async def test_turn_boundaries_reset_the_text(self):
        s = self.make()
        await self.pause(s, "Tell the other agent to")
        await s.handle_user_turn_stopped()
        self.assertEqual(s._text, "")
        self.assertIsNone(s.verdict())


class InTheAggregator(unittest.IsolatedAsyncioTestCase):
    """The strategy inside pipecat's own user aggregator and turn controller."""

    async def run_turn(self, text, prob, wait):
        import asyncio
        import time
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMUserAggregator, LLMUserAggregatorParams)
        from pipecat.tests.utils import SleepFrame, run_test
        from pipecat.turns.user_start.min_words_user_turn_start_strategy import MinWordsUserTurnStartStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies

        signal = EndOfTurnSignal()
        aggregator = LLMUserAggregator(LLMContext(), params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=2)],
                stop=[ForecastTurnStopStrategy(signal)])))
        stopped = []

        @aggregator.event_handler("on_user_turn_stopped")
        async def on_stopped(agg, strategy, message):
            stopped.append((time.monotonic(), message.content))

        async def forecast():
            while True:
                signal.update(steps(prob)[0])
                await asyncio.sleep(0.08)
        feed = asyncio.create_task(forecast())
        frames = [VADUserStartedSpeakingFrame(), TranscriptionFrame(text, "ahmed", "now"),
                  VADUserStoppedSpeakingFrame(stop_secs=0.2)]
        try:
            t0 = time.monotonic()
            await run_test(aggregator, frames_to_send=frames + [SleepFrame(wait)])
        finally:
            feed.cancel()
        return [(t - t0, content) for t, content in stopped]

    async def test_a_finished_sentence_ends_on_the_forecast(self):
        stopped = await self.run_turn("So what's on the docket?", 0.9, 1.2)
        self.assertEqual([c for _, c in stopped], ["So what's on the docket?"])
        self.assertLess(stopped[0][0], 0.8, "well before the session ends")

    async def test_a_dangling_word_waits_for_the_hold_limit(self):
        with patch.object(turn_end, "HOLD_SECS", 0.9):
            stopped = await self.run_turn("Tell the other agent to", 0.9, 1.8)
        self.assertEqual([c for _, c in stopped], ["Tell the other agent to"])
        self.assertGreaterEqual(stopped[0][0], 0.6, "not at the confident forecast")
        self.assertLess(stopped[0][0], 1.4, "at the hold limit, before the session ends")


class Transcriber(unittest.IsolatedAsyncioTestCase):
    async def test_steps_feed_the_forecast_and_text_still_flows(self):
        stt = ForecastGradiumSTTService(api_key="test")
        self.assertFalse(stt._enable_turn_detection, "flushes stay on the local pause")
        stt._handle_text = AsyncMock()
        stt._handle_flushed = AsyncMock()
        await stt._on_message({"type": "step", "vad": steps(0.7)[0]})
        await stt._on_message({"type": "text", "text": "hello"})
        await stt._on_message({"type": "flushed", "flush_id": "1"})
        self.assertEqual(stt.forecast.prob, 0.7)
        stt._handle_text.assert_awaited_once_with("hello")
        stt._handle_flushed.assert_awaited_once()


class Tap(unittest.IsolatedAsyncioTestCase):
    async def test_words_reach_the_manager_and_every_frame_passes(self):
        heard = []

        class M:
            def words_heard(self, text):
                heard.append(text)

        tap = WordsTap(M())
        tap.push_frame = AsyncMock()
        frames = [InterimTranscriptionFrame("So what's", "ahmed", "now"),
                  TranscriptionFrame("So what's on the docket?", "ahmed", "now"),
                  TranscriptionFrame("  ", "ahmed", "now"), VADUserStartedSpeakingFrame()]
        with patch.object(FrameProcessor, "process_frame", AsyncMock()):
            for f in frames:
                await tap.process_frame(f, FrameDirection.DOWNSTREAM)
        self.assertEqual(heard, ["So what's", "So what's on the docket?"])
        self.assertEqual([c.args[0] for c in tap.push_frame.await_args_list], frames)


if __name__ == "__main__":
    unittest.main()
