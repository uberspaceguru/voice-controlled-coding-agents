"""An in-flight invitation cannot overwrite the stage while input is judged."""

import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from test_dialogue_manager import TARGETS, judgment, manager

from dialogue_manager import CURRENT_TURN, TurnGuard


class StageLifecycle(unittest.IsolatedAsyncioTestCase):
    async def run_stage_race(self, next_act):
        m = manager()
        m.dialogue.sync("alpha", TARGETS)
        epoch = m.dialogue.begin()
        reading, finish_read = asyncio.Event(), asyncio.Event()
        judging, finish_judgment = asyncio.Event(), asyncio.Event()
        read_returned = asyncio.Event()
        m._app_speaks = AsyncMock()
        m._brief.return_value = {"sessionId": "beta", "recap": "The tests passed."}
        m._say.return_value = True

        async def next_session():
            reading.set()
            await finish_read.wait()
            read_returned.set()
            return TARGETS[1].copy()

        async def classify(*args):
            judging.set()
            await finish_judgment.wait()
            return judgment(next_act, target="beta", source="pending",
                            response="silent" if next_act == "ack" else "clarification",
                            route="none", execute=0)

        async def invite():
            token = CURRENT_TURN.set(TurnGuard(epoch, "alpha"))
            try:
                await m._do_invite_next("Next agent.", None, None)
            finally:
                CURRENT_TURN.reset(token)

        m._next_session = next_session
        m._jev.ask.side_effect = classify
        with ExitStack() as patches:
            patches.enter_context(patch("manager.note"))
            patches.enter_context(patch("manager.emit", AsyncMock()))
            patches.enter_context(patch("dialogue_manager.emit", AsyncMock()))
            m._active_work = asyncio.create_task(invite())
            original = m._active_work
            await reading.wait()
            m._schedule_dialogue("Mm-hmm." if next_act == "ack" else "No, the other agent.",
                                 None, None, "new_input")
            await judging.wait()
            finish_read.set()
            await read_returned.wait()
            await asyncio.sleep(0)
            # The old selection read has finished, but a semantic decision is
            # pending: it must not change the stage under that classifier.
            self.assertEqual(m.stage["sessionId"], "alpha")
            self.assertFalse(m._input_ready.is_set())
            finish_judgment.set()
            await m._handler
            result = await asyncio.gather(original, return_exceptions=True)
        return m, result[0]

    async def test_empty_final_invalidates_old_work_before_releasing_pause(self):
        m = manager()
        m.dialogue.sync("alpha", TARGETS)
        old_epoch = m.dialogue.begin()
        prepared = m.dialogue.prepare("Run tests.", "alpha")
        m.dialogue.mark_offered(prepared.pending_id)
        m._pause_for_input()
        entered = asyncio.Event()
        async def active():
            entered.set()
            await m._input_ready.wait()
            self.fail("Unclassified empty input released an old action")
        m._active_work = asyncio.create_task(active())
        await entered.wait()
        with patch("dialogue_manager.emit", AsyncMock()):
            self.assertTrue(await m.empty_input_stopped())
        result = await asyncio.gather(m._active_work, return_exceptions=True)
        self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertGreater(m.dialogue.epoch, old_epoch)
        self.assertFalse(m.dialogue.pending.offered)
        self.assertTrue(m._input_ready.is_set())

    async def test_empty_final_does_not_overtake_classifier_or_held_fragment(self):
        for kind in ("classifier", "held"):
            m = manager()
            m._pause_for_input()
            epoch = m.dialogue.epoch
            release = asyncio.Event()
            if kind == "classifier":
                m._judging = asyncio.create_task(release.wait())
            else:
                m._held = "Tell the other agent to"
            self.assertFalse(await m.empty_input_stopped())
            self.assertFalse(m._input_ready.is_set())
            self.assertEqual(m.dialogue.epoch, epoch)
            if m._judging:
                self.assertFalse(m._judging.cancelled())
                release.set()
                await m._judging
            else:
                self.assertEqual(m._held, "Tell the other agent to")

    async def test_correction_cancels_invite_without_losing_the_new_turn(self):
        m, result = await self.run_stage_race("correct")
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertEqual(m.stage["sessionId"], "alpha")
        m._app_speaks.assert_not_awaited()
        self.assertEqual(m.dialogue.recent[-1]["act"], "correct")

    async def test_backchannel_preserves_and_releases_the_original_invite(self):
        m, result = await self.run_stage_race("ack")
        self.assertIsNone(result)
        self.assertEqual(m.stage["sessionId"], "beta")
        m._app_speaks.assert_awaited_once()
        self.assertEqual(m.dialogue.recent[-1]["act"], "ack")


if __name__ == "__main__":
    unittest.main()
