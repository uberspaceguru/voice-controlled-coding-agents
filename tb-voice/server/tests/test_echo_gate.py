"""Pre-STT half-duplex gating with installed provider and transport methods.

Synthetic PCM and fake sockets only: this is not an acoustic echo or live
barge-in test. Output delivery is still tracked by its separate observer.
"""

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.transports.base_output import BaseOutputTransport
from websockets.protocol import State

from echo import EchoGate
from mute import EXTERNAL_UNTIL
from speech_delivery import DeliveryBook, OutputDeliveryObserver

PCM = b"\x01\x04" * 1280  # 80 ms of non-silent synthetic mono 16 kHz PCM.


def microphone():
    frame = InputAudioRawFrame(PCM, sample_rate=16000, num_channels=1)
    frame.pts = 123456
    frame.metadata["source"] = "synthetic"
    return frame


class EchoGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.addCleanup(patch.stopall)
        patch("echo.time", SimpleNamespace(monotonic=lambda: self.now)).start()
        patch.object(FrameProcessor, "process_frame", AsyncMock()).start()
        self.old_external_until = EXTERNAL_UNTIL["t"]
        EXTERNAL_UNTIL["t"] = 0.0
        self.addCleanup(EXTERNAL_UNTIL.__setitem__, "t", self.old_external_until)
        self.gate = EchoGate()
        self.gate.push_frame = AsyncMock()

    async def input(self):
        frame = microphone()
        await self.gate.process_frame(frame, FrameDirection.DOWNSTREAM)
        self.gate.push_frame.assert_awaited_with(frame, FrameDirection.DOWNSTREAM)
        return frame

    async def test_open_microphone_and_unrelated_control_frames_pass_unchanged(self):
        frame = await self.input()
        self.assertEqual(frame.audio, PCM)
        control = InterruptionFrame()
        await self.gate.process_frame(control, FrameDirection.UPSTREAM)
        self.gate.push_frame.assert_awaited_with(control, FrameDirection.UPSTREAM)
        self.assertFalse(self.gate.gated())

    async def test_bot_voice_is_silenced_before_stt_with_timing_preserved(self):
        await self.gate.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        frame = await self.input()
        self.assertEqual(frame.audio, bytes(len(PCM)))
        self.assertEqual((frame.sample_rate, frame.num_channels, frame.num_frames), (16000, 1, 1280))
        self.assertEqual(frame.pts, 123456)
        self.assertEqual(frame.metadata, {"source": "synthetic"})
        self.now += 20
        self.assertEqual((await self.input()).audio, bytes(len(PCM)))

    async def test_upstream_stop_keeps_tail_then_restores_microphone(self):
        await self.gate.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.gate.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        self.now += .59
        self.assertEqual((await self.input()).audio, bytes(len(PCM)))
        self.now += .02
        self.assertEqual((await self.input()).audio, PCM)
        # New output while the previous tail is active starts a new gate.
        await self.gate.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        self.now += 1
        self.assertEqual((await self.input()).audio, bytes(len(PCM)))

    async def test_native_voice_estimate_outlives_bot_stop(self):
        EXTERNAL_UNTIL["t"] = self.now + 5
        self.assertEqual((await self.input()).audio, bytes(len(PCM)))
        await self.gate.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        self.now += 1
        self.assertEqual((await self.input()).audio, bytes(len(PCM)))
        self.now += 4.01
        self.assertEqual((await self.input()).audio, PCM)

    async def test_installed_gradium_receives_zeros_continuously_during_output(self):
        sent = []
        async def send(message):
            sent.append(json.loads(message))
        stt = GradiumSTTService(api_key="synthetic-unused", sample_rate=16000)
        stt._chunk_size_bytes = len(PCM)
        stt._websocket = SimpleNamespace(state=State.OPEN, send=send)
        stt.push_frame = AsyncMock()
        self.gate.push_frame = stt.process_frame
        await self.gate.process_frame(microphone(), FrameDirection.DOWNSTREAM)
        await self.gate.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.gate.process_frame(microphone(), FrameDirection.DOWNSTREAM)
        await self.gate.process_frame(microphone(), FrameDirection.DOWNSTREAM)
        await self.gate.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        self.now += .61
        await self.gate.process_frame(microphone(), FrameDirection.DOWNSTREAM)
        self.assertEqual([base64.b64decode(message["audio"]) for message in sent],
                         [PCM, bytes(len(PCM)), bytes(len(PCM)), PCM])
        self.assertEqual([message["type"] for message in sent], ["audio"] * 4)

    async def test_real_transport_interruption_broadcast_releases_gate_and_preserves_delivery_state(self):
        # Use the installed MediaSender's own paired upstream/downstream
        # BotStarted/Stopped frames, including its interruption stop path.
        book = DeliveryBook(on_change=lambda event: None)
        delivery = book.create("Synthetic output.")
        book.bind(delivery, "active", "Synthetic output.")
        observer = OutputDeliveryObserver(book)
        observer.push_frame = AsyncMock()
        stt = GradiumSTTService(api_key="synthetic-unused")
        stt.push_frame = self.gate.process_frame
        broadcasts = []
        async def transport_push(frame, direction=FrameDirection.DOWNSTREAM):
            broadcasts.append((frame, direction))
            if direction == FrameDirection.UPSTREAM:
                await stt.process_frame(frame, direction)
            else:
                await observer.process_frame(frame, direction)
        sender = object.__new__(BaseOutputTransport.MediaSender)
        sender._bot_speaking = False
        sender._destination = None
        sender._tts_audio_received = False
        sender._audio_buffer = bytearray()
        sender._resampler = SimpleNamespace(reset=AsyncMock())
        sender._transport = SimpleNamespace(push_frame=transport_push)
        sender._audio_queue = SimpleNamespace(has_uninterruptible=False)
        sender._mixer = None
        for name in ("_cancel_clock_task", "_cancel_video_task", "_cancel_audio_task"):
            setattr(sender, name, AsyncMock())
        for name in ("_create_clock_task", "_create_video_task", "_create_audio_task"):
            setattr(sender, name, Mock())
        await sender._bot_started_speaking()
        self.assertTrue(self.gate.gated())
        self.assertEqual((await self.input()).audio, bytes(len(PCM)))
        interruption = InterruptionFrame()
        await sender.handle_interruptions(interruption)
        await observer.process_frame(interruption, FrameDirection.DOWNSTREAM)
        self.assertEqual(delivery.status, "interrupted")
        self.assertTrue(self.gate.gated())  # Stop does not skip the reverb tail.
        self.now += .61
        self.assertEqual((await self.input()).audio, PCM)
        self.assertEqual([type(frame) for frame, direction in broadcasts if direction == FrameDirection.UPSTREAM],
                         [BotStartedSpeakingFrame, BotStoppedSpeakingFrame])
        for start in range(0, len(broadcasts), 2):
            down, up = broadcasts[start][0], broadcasts[start + 1][0]
            self.assertEqual(down.broadcast_sibling_id, up.id)
            self.assertEqual(up.broadcast_sibling_id, down.id)
        # Delayed completion markers cannot reclassify interrupted output.
        book.generated_audio("active", TTSAudioRawFrame(PCM, 16000, 1, context_id="active"))
        book.provider_end("active")
        book.generation_complete("active")
        await observer.process_frame(TTSStoppedFrame(context_id="active"), FrameDirection.DOWNSTREAM)
        self.assertEqual(delivery.status, "interrupted")


if __name__ == "__main__":
    unittest.main()
