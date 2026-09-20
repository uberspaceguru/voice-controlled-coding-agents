"""Read-error receipts retain turn identity after the inner handler unwinds."""

import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import TTSAudioRawFrame
from pipecat.services.gradium.tts import GradiumTTSService
from test_memory_manager import TARGETS, SpeechEvidence, make_manager

from dialogue_manager import CURRENT_TURN
from manager import FleetReadError
from tts import SpokenGradiumTTSService

ERRORS = (FleetReadError, FileNotFoundError)


class ReadErrorDelivery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.emit = self.patches.enter_context(patch("manager.emit", AsyncMock()))
        for module in ("dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))

    async def test_current_read_errors_emit_guarded_receipts_and_restore_context(self):
        for error in ERRORS:
            with self.subTest(error=error.__name__):
                m = make_manager()
                speech = SpeechEvidence(m)
                m._targets.side_effect = error("synthetic read failure")
                await m._handle_turn("List the live agents.", None, None)
                self.assertEqual(len(speech.frames), 1)
                frame = speech.frames[0]
                self.assertIn("can't read", frame.text)
                self.assertTrue(callable(frame.current))
                self.assertTrue(frame.current())
                self.assertIs(frame.current, frame.delivery.current)
                self.assertIsNone(CURRENT_TURN.get())
                m._jev.ask.assert_not_awaited()

    async def test_failed_old_read_cannot_answer_new_input_or_switched_stage(self):
        for error in ERRORS:
            for supersession in ("input", "stage"):
                with self.subTest(error=error.__name__, supersession=supersession):
                    m = make_manager()
                    speech = SpeechEvidence(m)
                    started, release = asyncio.Event(), asyncio.Event()
                    async def read():
                        started.set()
                        await release.wait()
                        raise error("synthetic late failure")
                    m._targets.side_effect = read
                    task = asyncio.create_task(m._handle_turn("List the live agents.", None, None))
                    await started.wait()
                    if supersession == "input":
                        m._pause_for_input()
                    else:
                        m.stage = TARGETS[1].copy()
                    release.set()
                    await task
                    self.assertEqual(speech.frames, [])
                    m._jev.ask.assert_not_awaited()

    async def test_new_input_during_error_event_prevents_receipt_enqueue(self):
        for error in ERRORS:
            with self.subTest(error=error.__name__):
                m = make_manager()
                speech = SpeechEvidence(m)
                m._targets.side_effect = error("synthetic failure")
                async def event(processor, name, **fields):
                    if name == "error":
                        m._pause_for_input()
                self.emit.side_effect = event
                await m._handle_turn("List the live agents.", None, None)
                self.assertEqual(speech.frames, [])
        self.emit.side_effect = None

    async def test_queued_error_audio_is_invalidated_by_new_epoch_or_stage(self):
        for error in ERRORS:
            for supersession in ("epoch", "stage"):
                with self.subTest(error=error.__name__, supersession=supersession):
                    m = make_manager()
                    m._targets.side_effect = error("synthetic failure")
                    queued = asyncio.Event()
                    frames = []
                    async def enqueue(frame):
                        frames.append(frame)
                        queued.set()
                    m.push_frame = AsyncMock(side_effect=enqueue)
                    task = asyncio.create_task(m._handle_turn("List the live agents.", None, None))
                    await queued.wait()
                    frame = frames[0]
                    self.assertTrue(frame.current())
                    if supersession == "epoch":
                        m.dialogue.begin()
                    else:
                        m.stage = TARGETS[1].copy()
                    self.assertFalse(frame.current())
                    # The guard survives ContextVar restoration and is usable
                    # from the independent provider receive task.
                    service = object.__new__(SpokenGradiumTTSService)
                    service._dialogue_context_guards = {"error-receive": frame.current}
                    late_audio = TTSAudioRawFrame(b"\0" * 960, 24000, 1, context_id="error-receive")
                    with patch.object(GradiumTTSService, "append_to_audio_context", AsyncMock()) as provider_queue:
                        await service.append_to_audio_context("error-receive", late_audio)
                        provider_queue.assert_not_awaited()
                    m.deliverybook.finish(frame.delivery, "interrupted", "synthetic_supersession")
                    await task
                    self.assertEqual(len(frames), 1)
                    self.assertIsNone(CURRENT_TURN.get())


if __name__ == "__main__":
    unittest.main()
