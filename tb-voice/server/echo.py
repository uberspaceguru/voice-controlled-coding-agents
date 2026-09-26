"""Silence microphone PCM before STT while the manager or native app speaks.

Port of Robert's manager-mode EchoGate. Aggregator muting happens after STT,
so a provider can otherwise return a delayed transcript of our own speech
after the mute window ends. Keep frames and their timing, but replace their
samples with zero while output is active and during the bot's reverb tail.

This is half-duplex gating, not acoustic echo cancellation: user speech,
including a spoken stop request, is also inaudible to STT during the window.
The native-app window is the existing EXTERNAL_UNTIL estimate, not measured
playback completion. Already-buffered provider audio cannot be recalled.

`cancels_own_voice` (the Director app's WebRTC audio, local_rtc.py): the app's
engine has already removed the manager's own voice from the microphone, so the
gate stays open while it speaks and he can talk over it. The app's own voice
(a right-hand's answer on its card, EXTERNAL_UNTIL) is not in that engine's
reference, so that window still gates.
"""

import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from mute import EXTERNAL_UNTIL


class EchoGate(FrameProcessor):
    def __init__(self, tail_secs: float = 0.6, cancels_own_voice: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._cancels_own_voice = cancels_own_voice
        self._speaking = False
        self._stopped_at = 0.0
        self._tail = tail_secs

    def gated(self) -> bool:
        now = time.monotonic()
        if self._cancels_own_voice:
            return now < EXTERNAL_UNTIL["t"]
        return (self._speaking or (now - self._stopped_at) < self._tail
                or now < EXTERNAL_UNTIL["t"])

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking = False
            self._stopped_at = time.monotonic()
        elif isinstance(frame, InputAudioRawFrame) and self.gated():
            frame.audio = bytes(len(frame.audio))
        await self.push_frame(frame, direction)
