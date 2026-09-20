"""Gradium TTS, with the spoken-text rules applied to every sentence, and every
sentence it speaks written to the transcript as the manager's own line. This is
the one place all of the manager's speech passes, whichever path produced it."""

import asyncio
from contextvars import ContextVar

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    ErrorFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.gradium.tts import GradiumTTSService

from exact_speech import DialogueSpeakFrame, ExactSpeakFrame
from spoken import spoken

_literal = ContextVar("recorded_exact_speech", default=None)
_speech = ContextVar("dialogue_speech", default=None)
MAX_CONTEXT_GUARDS = 128


class SpokenGradiumTTSService(GradiumTTSService):
    def _bind_delivery(self, context_id, generated_text=""):
        book = getattr(self, "deliverybook", None)
        delivery = getattr(_speech.get(), "delivery", None)
        return book.bind(delivery, context_id, generated_text) if book and delivery else True

    def _settle_delivery(self, context_id, status, reason):
        book = getattr(self, "deliverybook", None)
        if book:
            book.finish(book.for_context(context_id), status, reason)

    def _context_current(self, context_id):
        guard = getattr(self, "_dialogue_context_guards", {}).get(context_id)
        return guard is None or guard()

    async def append_to_audio_context(self, context_id, frame):
        # Streaming providers receive audio on a separate task after run_tts
        # has returned and its ContextVars were reset. Keep validity with the
        # audio context, and allow completion sentinels to drain stale contexts.
        if isinstance(frame, TTSStartedFrame) and not self._bind_delivery(context_id):
            return
        if frame is not None and not isinstance(frame, TTSStoppedFrame):
            if not self._context_current(context_id):
                self._settle_delivery(context_id, "interrupted", "superseded")
                return
        book = getattr(self, "deliverybook", None)
        if book and isinstance(frame, TTSAudioRawFrame):
            book.generated_audio(context_id, frame)
        elif book and isinstance(frame, TTSStoppedFrame):
            # The installed provider's end_of_stream enqueues this marker.
            # Framework timeouts and provider errors push stop directly; they
            # must not be mistaken for natural completion of synthesis.
            book.provider_end(context_id)
        await super().append_to_audio_context(context_id, frame)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        # A chunk may already be queued when a new turn supersedes it. Recheck
        # when it leaves the TTS queue; audio already handed to the transport
        # remains owned by the framework's existing interruption mechanism.
        if isinstance(frame, (TTSAudioRawFrame, TTSTextFrame, TTSStartedFrame, AggregatedTextFrame)):
            if not self._context_current(getattr(frame, "context_id", None)):
                self._settle_delivery(getattr(frame, "context_id", None), "interrupted", "superseded")
                return
        book = getattr(self, "deliverybook", None)
        if book and isinstance(frame, TTSAudioRawFrame):
            book.generated_audio(frame.context_id, frame)
        await super().push_frame(frame, direction)

    async def push_error_frame(self, error, force_treat_as_permanent=False):
        # Provider receive errors may lack a context ID. Fail every in-flight
        # synthesis on this service; never turn its preceding stop into success.
        book = getattr(self, "deliverybook", None)
        if book:
            for delivery in list(book.records.values()):
                if delivery.context_id and not delivery.synthesis_complete:
                    book.finish(delivery, "failed", "provider_error")
        await super().push_error_frame(error, force_treat_as_permanent=force_treat_as_permanent)

    async def _record_context_audio_outcome(self, context_id, received_audio):
        # Deliberately discarded speech is not a silent-provider failure.
        if not self._context_current(context_id):
            if not self._bot_speaking:
                await self._maybe_resume_frame_processing()
            return
        await super()._record_context_audio_outcome(context_id, received_audio)

    async def on_audio_context_completed(self, context_id):
        try:
            await super().on_audio_context_completed(context_id)
            book = getattr(self, "deliverybook", None)
            if book:
                book.generation_complete(context_id)
        finally:
            getattr(self, "_dialogue_context_guards", {}).pop(context_id, None)

    async def on_audio_context_interrupted(self, context_id):
        self._settle_delivery(context_id, "interrupted", "audio_context_interrupted")
        try:
            await super().on_audio_context_interrupted(context_id)
        finally:
            getattr(self, "_dialogue_context_guards", {}).pop(context_id, None)

    async def _handle_interruption(self, frame, direction):
        book = getattr(self, "deliverybook", None)
        if book:
            book.interrupt("tts_interruption")
        interrupted = set(getattr(self, "_dialogue_context_guards", {}))
        try:
            await super()._handle_interruption(frame, direction)
        finally:
            # A fresh context may be registered while framework cleanup awaits.
            # Never erase its protection along with the interrupted generation.
            guards = getattr(self, "_dialogue_context_guards", {})
            for context_id in interrupted:
                guards.pop(context_id, None)

    async def stop(self, frame):
        try:
            await super().stop(frame)
        finally:
            getattr(self, "_dialogue_context_guards", {}).clear()

    async def cancel(self, frame):
        book = getattr(self, "deliverybook", None)
        if book:
            book.interrupt("tts_canceled")
        try:
            await super().cancel(frame)
        finally:
            getattr(self, "_dialogue_context_guards", {}).clear()

    async def process_frame(self, frame, direction):
        # Pipecat 1.11 awaits run_tts within TTSSpeakFrame processing. Scope the
        # exception to this frame/task, including failures and cancellation.
        value = frame.value if isinstance(frame, ExactSpeakFrame) else None
        token = _literal.set(value)
        speech = frame if isinstance(frame, DialogueSpeakFrame) else None
        speech_token = _speech.set(speech)
        try:
            if speech and speech.current and not speech.current():
                book = getattr(self, "deliverybook", None)
                if book:
                    book.finish(getattr(speech, "delivery", None), "interrupted", "superseded_before_synthesis")
                return
            await super().process_frame(frame, direction)
        except asyncio.CancelledError:
            book = getattr(self, "deliverybook", None)
            if book:
                book.finish(getattr(speech, "delivery", None), "interrupted", "synthesis_canceled")
            raise
        except Exception:
            book = getattr(self, "deliverybook", None)
            if book:
                book.finish(getattr(speech, "delivery", None), "failed", "synthesis_failed")
            raise
        finally:
            _literal.reset(token)
            _speech.reset(speech_token)

    async def run_tts(self, text: str, context_id: str):
        value = _literal.get()
        speech = _speech.get()
        if speech and speech.current:
            if not speech.current():
                return
            guards = getattr(self, "_dialogue_context_guards", None)
            if guards is None:
                guards = self._dialogue_context_guards = {}
            if context_id not in guards and len(guards) >= MAX_CONTEXT_GUARDS:
                # Never evict an active guard and accidentally turn stale audio
                # into unguarded audio. Existing contexts must settle first.
                logger.warning("TTS dialogue context capacity reached; refusing new speech")
                book = getattr(self, "deliverybook", None)
                if book:
                    book.finish(getattr(speech, "delivery", None), "failed", "tts_context_capacity")
                return
            guards[context_id] = speech.current
        clean = value.value if value is not None else spoken(
            text, max_words=120 if speech and speech.response_mode == "detail" else 30)
        if clean != text.strip():
            logger.info(f"spoken: {text[:80]!r} -> {clean[:80]!r}")
        from manager import note  # late import: manager imports events, not tts

        if not self._bind_delivery(context_id, clean):
            return
        note("Tranquility", clean, "generated")
        try:
            async for frame in super().run_tts(clean, context_id):
                if speech and speech.current and not speech.current():
                    self._settle_delivery(context_id, "interrupted", "superseded")
                    return
                if isinstance(frame, ErrorFrame):
                    self._settle_delivery(context_id, "failed", "provider_error")
                yield frame
        except asyncio.CancelledError:
            self._settle_delivery(context_id, "interrupted", "provider_request_canceled")
            raise
        except Exception:
            self._settle_delivery(context_id, "failed", "provider_request_failed")
            raise
