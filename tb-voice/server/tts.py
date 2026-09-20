"""Gradium TTS, with the spoken-text rules applied to every sentence, and every
sentence it speaks written to the transcript as the manager's own line. This is
the one place all of the manager's speech passes, whichever path produced it."""

from contextvars import ContextVar

from loguru import logger
from pipecat.services.gradium.tts import GradiumTTSService

from exact_speech import ExactSpeakFrame
from spoken import spoken

_literal = ContextVar("recorded_exact_speech", default=None)


class SpokenGradiumTTSService(GradiumTTSService):
    async def process_frame(self, frame, direction):
        # Pipecat 1.11 awaits run_tts within TTSSpeakFrame processing. Scope the
        # exception to this frame/task, including failures and cancellation.
        value = frame.value if isinstance(frame, ExactSpeakFrame) else None
        token = _literal.set(value)
        try:
            await super().process_frame(frame, direction)
        finally:
            _literal.reset(token)

    async def run_tts(self, text: str, context_id: str):
        value = _literal.get()
        clean = value.value if value is not None else spoken(text)
        if clean != text.strip():
            logger.info(f"spoken: {text[:80]!r} -> {clean[:80]!r}")
        from manager import note  # late import: manager imports events, not tts

        note("Tranquility", clean, "spoken")
        async for frame in super().run_tts(clean, context_id):
            yield frame
