"""Late speech joins the turn whose reply has not started; a finished reply is
never held behind sound that carries no words."""

import asyncio
import os
import time
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import director_link as d
import dialogue_manager
import test_director_link  # noqa: F401  (a roster of its own, never the Mac's)
from manager import Manager
from test_memory_manager import SpeechEvidence, make_manager

DIRECTOR = {"TB_DEFAULT_INTERLOCUTOR": "director"}


class Asks:
    """`director ask` that answers only when the test says so."""

    def __init__(self, reply="Ten things need you."):
        self.argv, self.gates, self.cancelled = [], [], []
        self.reply = reply
        self.started = asyncio.Event()

    async def __call__(self, *argv, timeout=60):
        gate = asyncio.Event()
        self.argv.append(argv)
        self.gates.append(gate)
        self.started.set()
        try:
            await gate.wait()
        except asyncio.CancelledError:
            self.cancelled.append(argv)
            raise
        return 0, self.reply

    async def wait_for(self, n, timeout=2.0):
        async def reach():
            while len(self.argv) < n:
                self.started.clear()
                await self.started.wait()
        await asyncio.wait_for(reach(), timeout)

    def words(self):
        return [a[a.index("ask") + 1] for a in self.argv]   # the words follow "ask" (after --json)


class LateSpeech(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.patches.enter_context(patch.dict(os.environ, DIRECTOR))
        self.patches.enter_context(patch("manager.note"))
        self.patches.enter_context(patch("dialogue_manager.emit", AsyncMock()))
        self.emit = self.patches.enter_context(patch("manager.emit", AsyncMock()))
        self.patches.enter_context(patch.object(FrameProcessor, "process_frame", AsyncMock()))
        # No delay token inside these tests: they pin the merge, not the bridge.
        self.patches.enter_context(patch.object(d, "SHORT_BRIDGE_AFTER", 60.0))
        self.patches.enter_context(patch.object(d, "LONG_BRIDGE_AFTER", 60.0))
        self.asks = Asks()
        self.patches.enter_context(patch("manager._run", self.asks))
        self.m = make_manager()
        self.m._say = AsyncMock(return_value=True)
        self.m._follow_up_until = time.monotonic() + 600    # a conversation is open
        self.context = LLMContext([])

    def tearDown(self):
        self.patches.close()

    async def turn(self, text):
        self.context.add_message({"role": "user", "content": text})
        await self.m.process_frame(LLMContextFrame(self.context), FrameDirection.DOWNSTREAM)

    def merges(self):
        return [c for c in self.emit.await_args_list if c.kwargs.get("reason") == "merged_late_speech"]

    async def test_a_second_sentence_before_the_reply_joins_the_first(self):
        # 25 Sep 18:04: "So what's on the docket?" / "What do I need to know?"
        # 1.6 s apart were asked and answered twice, the second 11.6 s late.
        await self.turn("So what's on the docket?")
        first = self.m._handler
        await self.asks.wait_for(1)
        await self.turn("What do I need to know?")
        await self.asks.wait_for(2)
        self.assertTrue(first.cancelled() or first.done())
        self.assertEqual(self.asks.cancelled, [self.asks.argv[0]], "the superseded ask is stopped")
        self.assertEqual(self.asks.words(), ["So what's on the docket?",
                                            "So what's on the docket? What do I need to know?"])
        self.asks.gates[1].set()
        await self.m._handler
        self.assertEqual([c.args[0] for c in self.m._say.await_args_list], ["Ten things need you."],
                         "one answer, to the whole thing")
        self.assertEqual(len(self.merges()), 1)
        self.assertEqual(self.merges()[0].kwargs["text"], "So what's on the docket? What do I need to know?")

    async def test_the_tail_of_a_named_request_is_never_dropped(self):
        # 19:42:48-51: the tail had no name and fell outside the window, so it
        # was ignored while the first half was still being answered.
        self.m._follow_up_until = 0.0
        await self.turn("Director, what are all the tasks? Can you expand the tranquility?")
        await self.asks.wait_for(1)
        await self.turn("Director section so I can see what's.")
        await self.asks.wait_for(2)
        self.assertEqual(self.asks.words()[1], "what are all the tasks? Can you expand the tranquility? "
                                               "Director section so I can see what's.")
        self.asks.gates[1].set()
        await self.m._handler

    async def test_a_call_word_after_the_first_part_is_not_repeated(self):
        # 17:05:32: "What?" then "Hey, Director, what needs my attention?"
        await self.turn("Director, what?")
        await self.asks.wait_for(1)
        await self.turn("Hey, Director, what needs my attention?")
        await self.asks.wait_for(2)
        self.assertEqual(self.asks.words()[1], "what? what needs my attention?")
        self.asks.gates[1].set()
        await self.m._handler

    async def test_several_late_parts_all_join(self):
        await self.turn("Director, tell me about")
        await self.asks.wait_for(1)
        await self.turn("the GPU one.")
        await self.asks.wait_for(2)
        await self.turn("And who is working on it?")
        await self.asks.wait_for(3)
        self.assertEqual(self.asks.words()[2], "tell me about the GPU one. And who is working on it?")
        self.assertEqual(len(self.asks.cancelled), 2)
        self.asks.gates[2].set()
        await self.m._handler
        self.assertEqual(self.m._say.await_count, 1)

    async def test_speech_after_the_reply_started_is_its_own_turn(self):
        speaking, finish = asyncio.Event(), asyncio.Event()

        async def say(text, **kwargs):
            self.m._reply_starting()        # what the real _say does as the voice starts
            speaking.set()
            await finish.wait()
            return True
        self.m._say = AsyncMock(side_effect=say)
        await self.turn("Director, what needs me?")
        first = self.m._handler
        await self.asks.wait_for(1)
        self.asks.gates[0].set()
        await speaking.wait()
        await self.turn("Director, and what is ready?")
        await self.asks.wait_for(2)
        self.assertFalse(first.done(), "the reply being spoken is not cancelled by a merge")
        self.assertEqual(self.asks.words(), ["what needs me?", "and what is ready?"])
        self.assertEqual(self.merges(), [])
        finish.set()
        self.asks.gates[1].set()
        await asyncio.gather(first, self.m._handler)

    async def test_a_delay_token_is_not_the_reply(self):
        spoken = []

        async def say(text, **kwargs):
            self.m._reply_starting()
            spoken.append(text)
            return True
        self.m._say = AsyncMock(side_effect=say)
        with patch.object(d, "SHORT_BRIDGE_AFTER", 0.01):
            await self.turn("Director, tell me more about the GPU one.")
            await self.asks.wait_for(1)
            while not spoken:
                await asyncio.sleep(0.01)
            self.assertEqual(spoken, ["The GPU one. One sec."])
            await self.turn("Who is working on it?")
            await self.asks.wait_for(2)
        self.assertEqual(self.asks.words()[1], "tell me more about the GPU one. Who is working on it?")
        self.asks.gates[1].set()
        await self.m._handler

    async def test_stop_and_a_bare_call_are_not_merged(self):
        self.m.broadcast_interruption = AsyncMock()
        self.m._do_mute = AsyncMock()
        await self.turn("Director, what needs me?")
        first = self.m._handler
        await self.asks.wait_for(1)
        await self.turn("Stop.")
        await self.m._handler
        self.m._do_mute.assert_awaited_once()
        self.assertEqual(self.merges(), [])
        await self.turn("Hey, Director.")
        await self.m._handler
        self.assertEqual(self.merges(), [])
        self.assertEqual(len(self.asks.argv), 1)
        self.asks.gates[0].set()
        await first

    async def test_a_turn_already_answered_or_ignored_takes_nothing(self):
        await self.turn("Director, what needs me?")
        await self.asks.wait_for(1)
        self.asks.gates[0].set()
        await self.m._handler
        await self.turn("What else?")          # a follow-up: a new ask, not a merge
        await self.asks.wait_for(2)
        self.asks.gates[1].set()
        await self.m._handler
        self.assertEqual(self.asks.words(), ["what needs me?", "What else?"])
        self.assertEqual(self.merges(), [])

    async def test_outside_the_director_app_nothing_merges(self):
        with patch.dict(os.environ, {"TB_DEFAULT_INTERLOCUTOR": ""}):
            self.assertIsNone(self.m._merge_late("What do I need to know?"))


class Floor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.emit = self.patches.enter_context(patch("dialogue_manager.emit", AsyncMock()))
        self.patches.enter_context(patch.object(dialogue_manager, "FLOOR_WAIT_SECS", 0.15))
        self.patches.enter_context(patch.object(dialogue_manager, "HOLD_SECS", 0.3))
        self.m = make_manager()

    def tearDown(self):
        self.patches.close()

    def capped(self):
        return [c for c in self.emit.await_args_list if c.kwargs.get("reason") == "floor_wait_capped"]

    async def timed(self, coro):
        t0 = time.monotonic()
        await coro
        return time.monotonic() - t0

    async def test_hearing_without_words_holds_a_reply_at_most_the_floor_wait(self):
        # 25 Sep 18:19: a reply was held 27.6 s while VAD heard the room.
        self.m._pause_for_input()
        self.m.words_heard("So that first one.")
        waited = await self.timed(self.m._floor_ready())
        self.assertGreaterEqual(waited, 0.14)
        self.assertLess(waited, 0.5)
        self.assertFalse(self.m._input_ready.is_set(), "the hearing pause itself is untouched")
        self.assertEqual(len(self.capped()), 1)

    async def test_words_still_coming_keep_the_reply_waiting(self):
        self.m._pause_for_input()

        async def talking():
            for _ in range(6):
                self.m.words_heard("Tell me more about that")
                await asyncio.sleep(0.05)
        talk = asyncio.create_task(talking())
        waited = await self.timed(self.m._floor_ready())
        await talk
        self.assertGreaterEqual(waited, 0.25 + 0.14, "waits until his words stop, then the floor wait")

    async def test_words_that_hold_the_floor_wait_longer(self):
        self.m._pause_for_input()
        self.m.words_heard("Tell me more about the")
        waited = await self.timed(self.m._floor_ready())
        self.assertGreaterEqual(waited, 0.55, "HOLD_SECS + 0.3")

    async def test_a_turn_being_judged_is_always_waited_for(self):
        self.m._pause_for_input()
        release = asyncio.Event()
        self.m._judging = asyncio.create_task(release.wait())

        async def settle():
            await asyncio.sleep(0.4)
            release.set()
            self.m._input_ready.set()
        asyncio.create_task(settle())
        waited = await self.timed(self.m._floor_ready())
        self.assertGreaterEqual(waited, 0.38)
        self.assertEqual(self.capped(), [])

    async def test_a_settled_input_does_not_wait(self):
        self.assertLess(await self.timed(self.m._floor_ready()), 0.05)

    async def test_the_rest_of_the_reply_does_not_wait_again(self):
        self.m._pause_for_input()
        await self.m._floor_ready()
        self.assertLess(await self.timed(self.m._floor_ready()), 0.05)
        self.m._pause_for_input()               # he starts again: a new wait
        self.assertGreaterEqual(await self.timed(self.m._floor_ready()), 0.14)

    async def test_the_real_say_speaks_over_wordless_hearing(self):
        SpeechEvidence(self.m)
        with patch("manager.note"), patch("manager.emit", AsyncMock()):
            self.m._pause_for_input()
            self.m.words_heard("So that first one.")
            self.assertTrue(await asyncio.wait_for(Manager._say(self.m, "It needs the GPU back."), 2.0))


if __name__ == "__main__":
    unittest.main()
