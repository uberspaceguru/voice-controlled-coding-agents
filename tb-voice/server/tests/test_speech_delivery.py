"""Correlated transport completion is observable output, never proof of hearing."""
import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.transports.base_output import BaseOutputTransport
from websockets.protocol import State

from exact_speech import DialogueSpeakFrame
from speech_delivery import DeliveryBook, OutputDeliveryObserver
from tts import SpokenGradiumTTSService, _speech


class ProviderSocket:
    state = State.OPEN

    def __init__(self):
        self.sent, self.incoming = [], []

    async def send(self, message):
        self.sent.append(json.loads(message))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        await asyncio.sleep(0)
        return json.dumps(self.incoming.pop(0))


def audio(context=None, seconds=.02):
    return TTSAudioRawFrame(audio=b"\0" * int(48000 * seconds * 2),
                           sample_rate=48000, num_channels=1, context_id=context)


def natural_generation_complete(book, context):
    book.provider_end(context)
    book.generation_complete(context)


def fixture(context="one"):
    book = DeliveryBook(on_change=lambda event: None)
    delivery = book.create("A factual answer.")
    book.bind(delivery, context, "A factual answer.")
    observer = OutputDeliveryObserver(book)
    observer.push_frame = AsyncMock()
    return book, delivery, observer


class OutputDelivery(unittest.IsolatedAsyncioTestCase):
    async def output(self, observer, *frames):
        with patch.object(FrameProcessor, "process_frame", AsyncMock()):
            for frame in frames:
                await observer.process_frame(frame, FrameDirection.DOWNSTREAM)

    async def test_requires_matching_output_and_generation_completion(self):
        book, delivery, observer = fixture()
        book.generated_audio("one", audio("one"))
        await self.output(observer, TTSStartedFrame(context_id="one"), audio(),
                          TTSStoppedFrame(context_id="one"))
        self.assertFalse(delivery.completion.is_set())
        natural_generation_complete(book, "one")
        self.assertTrue(await book.wait(delivery, .01))
        self.assertEqual(delivery.status, "output_complete")
        self.assertEqual(delivery.reason, "transport_output_complete")
        self.assertEqual(delivery.output_frames, 1)

    async def test_generic_bot_stop_never_completes_delivery(self):
        book, delivery, observer = fixture()
        book.generated_audio("one", audio("one"))
        natural_generation_complete(book, "one")
        await self.output(observer, TTSStartedFrame(context_id="one"), audio(), BotStoppedSpeakingFrame())
        self.assertFalse(delivery.completion.is_set())
        self.assertFalse(await book.wait(delivery, .001))
        self.assertEqual(delivery.status, "unknown")
        await self.output(observer, TTSStoppedFrame(context_id="one"))
        self.assertEqual(delivery.status, "unknown")

    async def test_partial_output_then_interruption_stays_interrupted(self):
        book, delivery, observer = fixture()
        book.generated_audio("one", audio("one", .1))
        await self.output(observer, TTSStartedFrame(context_id="one"), audio(), InterruptionFrame())
        natural_generation_complete(book, "one")
        await self.output(observer, TTSStoppedFrame(context_id="one"))
        self.assertEqual(delivery.status, "interrupted")
        self.assertFalse(await book.wait(delivery, .01))

    async def test_old_stop_cannot_complete_new_utterance(self):
        book, old, observer = fixture("old")
        book.finish(old, "interrupted", "superseded")
        new = book.create("The current answer.")
        book.bind(new, "new")
        book.generated_audio("new", audio("new"))
        natural_generation_complete(book, "new")
        await self.output(observer, TTSStartedFrame(context_id="new"), audio(),
                          TTSStoppedFrame(context_id="old"), BotStoppedSpeakingFrame())
        self.assertFalse(new.completion.is_set())
        await self.output(observer, TTSStoppedFrame(context_id="new"))
        self.assertEqual(new.status, "output_complete")
        self.assertEqual(old.status, "interrupted")

    async def test_provider_failure_cannot_be_promoted_by_natural_stop(self):
        book, delivery, observer = fixture()
        book.generated_audio("one", audio("one"))
        await self.output(observer, TTSStartedFrame(context_id="one"), audio())
        book.finish(delivery, "failed", "provider_error")
        natural_generation_complete(book, "one")
        await self.output(observer, TTSStoppedFrame(context_id="one"))
        self.assertEqual(delivery.status, "failed")

    async def test_overlapping_or_missing_start_fails_closed(self):
        book, first, observer = fixture("first")
        second = book.create("Second answer.")
        book.bind(second, "second")
        await self.output(observer, TTSStartedFrame(context_id="first"), TTSStartedFrame(context_id="second"))
        self.assertEqual(first.status, "unknown")
        self.assertEqual(second.status, "unknown")
        third = book.create("Third answer.")
        book.bind(third, "third")
        book.generated_audio("third", audio("third"))
        natural_generation_complete(book, "third")
        await self.output(observer, TTSStoppedFrame(context_id="third"))
        self.assertEqual(third.status, "unknown")

    async def test_duplicate_audio_frames_do_not_hide_missing_audio(self):
        book, delivery, observer = fixture()
        generated = audio("one", .04)
        book.generated_audio("one", generated)
        book.generated_audio("one", generated)
        natural_generation_complete(book, "one")
        written = audio(seconds=.02)
        await self.output(observer, TTSStartedFrame(context_id="one"), written, written,
                          TTSStoppedFrame(context_id="one"))
        self.assertEqual(delivery.generated_frames, 1)
        self.assertEqual(delivery.output_frames, 1)
        self.assertEqual(delivery.status, "failed")
        self.assertEqual(delivery.reason, "incomplete_output_audio")

    async def test_no_audio_never_completes_output(self):
        for generated in (False, True):
            book, delivery, observer = fixture()
            if generated:
                book.generated_audio("one", audio("one"))
            natural_generation_complete(book, "one")
            await self.output(observer, TTSStartedFrame(context_id="one"), TTSStoppedFrame(context_id="one"))
            self.assertEqual(delivery.status, "failed")

    async def test_actual_transport_forwarding_requires_successful_audio_writes(self):
        # Installed transport loop omits failed writes; markers still pass.
        for successes, expected in (([True, True], "output_complete"),
                                    ([True, False], "failed"), ([False, False], "failed")):
            book, delivery, observer = fixture()
            book.generated_audio("one", audio("one", .04))
            natural_generation_complete(book, "one")
            frames = [TTSStartedFrame(context_id="one"), audio(), audio(), TTSStoppedFrame(context_id="one")]
            async def stream():
                for frame in frames:
                    yield frame
            async def forward(frame):
                await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
            sender = object.__new__(BaseOutputTransport.MediaSender)
            sender._next_frame = stream
            sender._handle_frame = AsyncMock()
            sender._params = SimpleNamespace(audio_out_write_timeout_secs=1)
            sender._transport = SimpleNamespace(is_usable=True,
                write_audio_frame=AsyncMock(side_effect=successes), push_frame=forward)
            with patch.object(FrameProcessor, "process_frame", AsyncMock()):
                await sender._audio_task_handler()
            self.assertEqual(delivery.status, expected)
            self.assertEqual(delivery.output_frames, sum(successes))

    async def test_expired_validity_blocks_completion(self):
        book, delivery, observer = fixture()
        current = {"value": True}
        delivery.current = lambda: current["value"]
        book.generated_audio("one", audio("one"))
        natural_generation_complete(book, "one")
        await self.output(observer, TTSStartedFrame(context_id="one"), audio())
        current["value"] = False
        await self.output(observer, TTSStoppedFrame(context_id="one"))
        self.assertEqual(delivery.status, "interrupted")

    async def test_real_provider_handoff_binds_before_start_and_completes_after_output(self):
        book = DeliveryBook(on_change=lambda _: None)
        delivery = book.create("A factual answer.")
        observer = OutputDeliveryObserver(book)
        observer.push_frame = AsyncMock()
        tts = SpokenGradiumTTSService(api_key="synthetic")
        tts.deliverybook = book
        tts._websocket = ProviderSocket()
        tts._sample_rate = 48000
        tts._audio_contexts = {"ctx": asyncio.Queue()}
        tts.start_tts_usage_metrics = AsyncMock()
        tts.stop_all_metrics = AsyncMock()
        speech = DialogueSpeakFrame(text=delivery.text, current=lambda: True)
        speech.delivery = delivery
        token = _speech.set(speech)
        try:
            # The installed service enqueues start BEFORE entering run_tts.
            await tts.append_to_audio_context("ctx", TTSStartedFrame(context_id="ctx"))
            self.assertEqual(delivery.context_id, "ctx")
            with patch("manager.note") as note:
                self.assertEqual([f async for f in tts.run_tts(speech.text, "ctx")], [None])
                note.assert_called_once_with("Tranquility", speech.text, "generated")
        finally:
            _speech.reset(token)
        self.assertFalse(delivery.completion.is_set())
        tts._websocket.incoming = [
            {"type": "audio", "client_req_id": "ctx", "audio": base64.b64encode(audio().audio).decode()},
            {"type": "end_of_stream", "client_req_id": "ctx"},
        ]
        await asyncio.create_task(tts._receive_messages())
        queue = tts._audio_contexts["ctx"]
        # The device-write success behavior is exercised independently above;
        # here the real provider receiver feeds the correlated output observer.
        while not queue.empty():
            frame = queue.get_nowait()
            if frame is not None:
                await self.output(observer, frame)
        self.assertFalse(delivery.completion.is_set())
        await tts.on_audio_context_completed("ctx")
        self.assertEqual(delivery.status, "output_complete")
        self.assertEqual(delivery.generated_frames, 1)
        self.assertEqual(delivery.output_frames, 1)
        self.assertEqual(delivery.generated_text, speech.text)

    async def test_provider_error_stop_does_not_complete_before_error_arrives(self):
        book, delivery, observer = fixture("ctx")
        tts = SpokenGradiumTTSService(api_key="synthetic")
        tts.deliverybook = book
        tts._websocket = ProviderSocket()
        tts.stop_all_metrics = AsyncMock()
        tts._audio_contexts = {"ctx": asyncio.Queue()}
        book.generated_audio("ctx", audio("ctx"))
        await self.output(observer, TTSStartedFrame(context_id="ctx"), audio())
        async def forward(service, frame, direction=FrameDirection.DOWNSTREAM):
            await self.output(observer, frame)
        # The installed provider reports TTSStopped first, then an ErrorFrame.
        tts._websocket.incoming = [{"type": "error", "client_req_id": "ctx", "message": "synthetic error"}]
        with patch.object(GradiumTTSService, "push_frame", forward), patch.object(GradiumTTSService, "push_error_frame", AsyncMock()):
            await tts._receive_messages()
        self.assertEqual(delivery.status, "failed")
        self.assertEqual(delivery.reason, "provider_error")
        natural_generation_complete(book, "ctx")
        self.assertEqual(delivery.status, "failed")

    async def test_partial_provider_stream_timeout_is_unknown_not_complete(self):
        book, delivery, observer = fixture("ctx")
        tts = SpokenGradiumTTSService(api_key="synthetic")
        tts.deliverybook = book
        tts._audio_contexts = {"ctx": asyncio.Queue()}
        tts._stop_frame_timeout_s = .001
        tts._apply_force_complete = AsyncMock()
        tts._maybe_reset_word_timestamps = AsyncMock()
        tts.stop_ttfb_metrics = AsyncMock()
        tts.start_word_timestamps = AsyncMock()
        tts.process_ttfa_metrics = AsyncMock()
        async def forward(frame, direction=FrameDirection.DOWNSTREAM):
            await self.output(observer, frame)
        tts.push_frame = forward
        await tts.append_to_audio_context("ctx", TTSStartedFrame(context_id="ctx"))
        await tts.append_to_audio_context("ctx", audio("ctx"))
        # No provider end_of_stream: the installed queue handler times out,
        # emits its own stop, and reports context completion after partial audio.
        await tts._handle_audio_context("ctx")
        await tts.on_audio_context_completed("ctx")
        self.assertEqual(delivery.output_frames, 1)
        self.assertTrue(delivery.output_stopped)
        self.assertFalse(delivery.provider_end_received)
        self.assertEqual(delivery.status, "unknown")
        self.assertEqual(delivery.reason, "provider_end_not_observed")

    async def test_book_is_bounded_without_reassigning_contexts(self):
        book = DeliveryBook(on_change=lambda _: None, limit=2)
        a, b = book.create("A"), book.create("B")
        self.assertTrue(book.bind(a, "a"))
        self.assertFalse(book.bind(b, "a"))
        self.assertEqual(b.status, "unknown")
        c = book.create("C")
        self.assertEqual(len(book.records), 2)
        self.assertIs(book.for_context("a"), a)
        self.assertEqual(book.create("D").status, "failed")
        self.assertFalse(c.completion.is_set())


if __name__ == "__main__":
    unittest.main()
