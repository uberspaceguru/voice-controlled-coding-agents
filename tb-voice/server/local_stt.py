"""Hearing on this Mac (27 Sep): Gradium's streaming transcriber billed every minute the microphone was open, all
day, and ran out of credits, so hands-free went deaf. Here the local VAD finds speech and MLX Whisper transcribes
only those stretches, on the Mac's GPU, at no cost; nothing is streamed anywhere. ~0.3 s per phrase once warm
(large-v3-turbo, measured 27 Sep).

pipecat's own WhisperSTTServiceMLX imports faster_whisper at module load; this needs only mlx_whisper.
"""

import asyncio
from typing import AsyncGenerator

import numpy as np
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

MODEL = "mlx-community/whisper-large-v3-turbo"
NO_SPEECH_BELOW = 0.6
# Whisper's stock hallucinations on near-silence or room noise
_PHANTOMS = {"", "you", "thank you.", "thanks for watching!", "thank you for watching.", "bye.", "."}


def transcribe(pcm: bytes, model: str = MODEL, rate: int = 16000) -> str:
    import mlx_whisper
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != 16000 and audio.size:            # Whisper hears 16 kHz
        n = int(audio.size * 16000 / rate)
        audio = np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio).astype(np.float32)
    if audio.size < 1600:                       # under 0.1 s: nothing a person said
        return ""
    out = mlx_whisper.transcribe(audio, path_or_hf_repo=model, language="en", temperature=0.0,
                                 condition_on_previous_text=False)
    words = " ".join(s.get("text", "").strip() for s in out.get("segments", [])
                     if s.get("no_speech_prob", 0.0) < NO_SPEECH_BELOW).strip()
    return "" if words.lower() in _PHANTOMS else words


class LocalWhisperSTTService(SegmentedSTTService):
    def __init__(self, *, model: str = MODEL, **kwargs):
        super().__init__(**kwargs)
        self._model = model

    @property
    def wants_wav_segments(self) -> bool:
        return False                            # raw 16-bit PCM

    def can_generate_metrics(self) -> bool:
        return True

    async def warm(self):
        """Load the model before he speaks: the first transcription otherwise takes ~2.4 s."""
        await asyncio.to_thread(transcribe, np.zeros(16000, dtype=np.int16).tobytes(), self._model)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        try:
            await self.start_processing_metrics()
            text = await asyncio.to_thread(transcribe, audio, self._model, self.sample_rate or 16000)
            await self.stop_processing_metrics()
        except Exception as e:                  # noqa: BLE001 - a bad segment must not end the session
            logger.error(f"local transcription failed: {e}")
            yield ErrorFrame(error=f"local transcription failed: {e}")
            return
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), Language.EN)
        yield None
