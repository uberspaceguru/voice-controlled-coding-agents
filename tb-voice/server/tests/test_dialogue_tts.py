"""Exercise streaming handoff separately from request-scoped ContextVars.

The provider socket is synthetic, but its installed run_tts and receive loop are
real. These tests prove context routing, not audible playback or provider service.
"""

import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.gradium.tts import GradiumTTSService
from websockets.protocol import State

from exact_speech import DialogueSpeakFrame
from tts import SpokenGradiumTTSService, _speech


class Socket:
    state = State.OPEN

    def __init__(self):
        self.sent = []
        self.incoming = []

    async def send(self, text):
        self.sent.append(json.loads(text))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        await asyncio.sleep(0)
        return json.dumps(self.incoming.pop(0))


def service():
    tts = SpokenGradiumTTSService(api_key="synthetic-test-key")
    tts._websocket = Socket()
    tts._sample_rate = 48000
    tts._audio_contexts = {}
    tts.start_tts_usage_metrics = AsyncMock()
    tts.stop_all_metrics = AsyncMock()
    return tts


async def request(tts, context, current=None):
    tts._audio_contexts[context] = asyncio.Queue()
    token = _speech.set(DialogueSpeakFrame(text="A fresh answer.", current=current))
    try:
        with patch("manager.note"):
            frames = [frame async for frame in tts.run_tts("A fresh answer.", context)]
    finally:
        _speech.reset(token)
    return frames


def audio(context):
    return TTSAudioRawFrame(audio=b"\x00\x01", sample_rate=48000,
                           num_channels=1, context_id=context)


class StreamingSpeech(unittest.IsolatedAsyncioTestCase):
    async def receive(self, tts, context):
        tts._websocket.incoming = [
            {"type": "audio", "client_req_id": context,
             "audio": base64.b64encode(b"\x00\x01").decode()},
            {"type": "end_of_stream", "client_req_id": context},
        ]
        # This task starts after request() reset the ContextVar, matching the
        # provider receiver rather than a patched generator yielding audio.
        self.assertIsNone(_speech.get())
        await asyncio.create_task(tts._receive_messages())
        queue = tts._audio_contexts[context]
        return [queue.get_nowait() for _ in range(queue.qsize())]

    async def test_late_provider_audio_is_dropped_after_request_context_resets(self):
        tts = service()
        active = {"value": True}
        frames = await request(tts, "old", lambda: active["value"])
        self.assertEqual(frames, [None])  # Real provider method hands off to socket.
        self.assertEqual(len(tts._websocket.sent), 2)  # Setup and text were accepted.
        active["value"] = False
        received = await self.receive(tts, "old")
        self.assertEqual(len(received), 2)
        self.assertIsInstance(received[0], TTSStoppedFrame)
        self.assertIsNone(received[1])  # The stale context can still finish.
        self.assertFalse(any(isinstance(frame, TTSAudioRawFrame) for frame in received))

    async def test_current_and_unguarded_provider_audio_remain_unchanged(self):
        for guard in (lambda: True, None):
            tts = service()
            await request(tts, "current", guard)
            received = await self.receive(tts, "current")
            self.assertIsInstance(received[0], TTSAudioRawFrame)
            self.assertEqual(received[0].audio, b"\x00\x01")
            self.assertIsInstance(received[1], TTSStoppedFrame)
            self.assertIsNone(received[2])

    async def test_already_queued_audio_is_rechecked_before_push(self):
        tts = service()
        active = {"value": True}
        await request(tts, "old", lambda: active["value"])
        await tts.append_to_audio_context("old", audio("old"))
        queued = tts._audio_contexts["old"].get_nowait()
        active["value"] = False
        with patch.object(GradiumTTSService, "push_frame", AsyncMock()) as push:
            await tts.push_frame(queued)
            push.assert_not_awaited()
            stop = TTSStoppedFrame(context_id="old")
            await tts.push_frame(stop)
            push.assert_awaited_once_with(stop, FrameDirection.DOWNSTREAM)

    async def test_guarded_silence_is_not_reported_as_provider_failure(self):
        tts = service()
        tts._dialogue_context_guards = {"old": lambda: False}
        tts._maybe_resume_frame_processing = AsyncMock()
        with patch.object(GradiumTTSService, "_record_context_audio_outcome", AsyncMock()) as outcome:
            await tts._record_context_audio_outcome("old", False)
            outcome.assert_not_awaited()
            tts._maybe_resume_frame_processing.assert_awaited_once()
            await tts._record_context_audio_outcome("ordinary", False)
            outcome.assert_awaited_once_with("ordinary", False)

    async def test_guards_are_bounded_without_evicting_live_contexts(self):
        tts = service()
        with patch("tts.MAX_CONTEXT_GUARDS", 2):
            await request(tts, "one", lambda: True)
            await request(tts, "two", lambda: True)
            sent = len(tts._websocket.sent)
            await request(tts, "three", lambda: True)
            self.assertEqual(len(tts._websocket.sent), sent)
            self.assertEqual(set(tts._dialogue_context_guards), {"one", "two"})
            await tts.on_audio_context_completed("one")
            self.assertEqual(set(tts._dialogue_context_guards), {"two"})
            await request(tts, "three", lambda: True)
            self.assertEqual(len(tts._websocket.sent), sent + 2)

    async def test_interruption_cleanup_preserves_concurrently_registered_guard(self):
        tts = service()
        tts._dialogue_context_guards = {"old": lambda: False}
        async def cleanup(*args):
            tts._dialogue_context_guards["new"] = lambda: False
        with patch.object(GradiumTTSService, "_handle_interruption", cleanup):
            await tts._handle_interruption(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(set(tts._dialogue_context_guards), {"new"})
        self.assertFalse(tts._context_current("new"))

    async def test_interruption_stop_and_cancel_release_context_guards(self):
        for method, args in (
            ("_handle_interruption", (InterruptionFrame(), FrameDirection.DOWNSTREAM)),
            ("stop", (EndFrame(),)),
            ("cancel", (CancelFrame(),)),
        ):
            tts = service()
            tts._dialogue_context_guards = {"old": lambda: False}
            with patch.object(GradiumTTSService, method, AsyncMock()) as parent:
                await getattr(tts, method)(*args)
                parent.assert_awaited_once_with(*args)
            self.assertEqual(tts._dialogue_context_guards, {})


if __name__ == "__main__":
    unittest.main()
