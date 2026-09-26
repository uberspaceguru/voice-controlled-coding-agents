"""When has Ahmed finished speaking? The end of a user turn.

His first real session (25 Sep) had 24 turns split mid-sentence: a pause of
about half a second ended the turn, and "So what's on the docket? / What do I
need to know?" became two asks and two answers. Pauses inside one speaker's
turn are longer than the gaps between speakers (Heldner & Edlund 2010: a
500 ms threshold cuts in at more than half of the within-turn pauses), so
silence alone decides nothing here (research/turn-taking.md, rules 1 and 2):

- The transcriber's own end-of-turn forecast decides. Every 80 ms it sends a
  "step" with, per horizon, the probability that the speaker stays quiet; the
  turn ends at a pause once that probability (2 s horizon) holds at or above
  0.5 for two steps in a row.
- A turn whose words end holding the floor (a filler "um", a word cut off,
  a dangling "and / so / because / to / the / of / but / or / with") is not
  ended by silence under 2.5 s, whatever the forecast says.
- Silence alone ends a turn only after 2.5 s (the fallback), or after 1.2 s
  when no forecast has arrived at all (a reconnect), as before.

The transcriber keeps flushing at each local VAD pause, so final text arrives
per pause exactly as before; only the decision of when the turn is over moved.
"""

import asyncio
import json
from collections import deque
import os
import re
import time

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy

HOLD_SECS = float(os.getenv("TB_HOLD_SECS", "2.5"))          # a turn holding the floor
FALLBACK_SECS = float(os.getenv("TB_EOT_FALLBACK_SECS", "2.5"))  # forecast says "not done"
NO_SIGNAL_SECS = float(os.getenv("TB_SPEECH_TIMEOUT", "1.2"))  # no forecast at all
EOT_HORIZON_S = float(os.getenv("TB_EOT_HORIZON", "2.0"))
EOT_THRESHOLD = float(os.getenv("TB_EOT_THRESHOLD", "0.5"))
EOT_STEPS = int(os.getenv("TB_EOT_STEPS", "2"))
SIGNAL_STALE_SECS = 0.5   # steps come every 80 ms; half a second without one is a gap
TICK_SECS = 0.05

FILLERS = {"um", "uh", "umm", "uhh", "uhm", "erm", "er", "hmm", "mm", "mmm"}
DANGLING = {"and", "so", "because", "cause", "cuz", "but", "or", "nor", "to", "the", "an",
            "of", "with", "for", "from", "about", "into", "than", "if", "my", "your",
            "our", "their"}
# "I think so." ends a turn; "so" after these is not a dangling conjunction.
_SO_COMPLETE = {"think", "hope", "guess", "believe", "said", "say", "not", "so", "do"}
# A contraction left with nothing after it: "…so I can see what's."
CUT_CONTRACTIONS = {"what's", "it's", "that's", "there's", "here's", "who's", "where's",
                    "i'm", "you're", "we're", "they're", "he's", "she's", "let's"}
_CUT_MARK = re.compile(r"(?:-|—|–|\.\.\.|…|,)\s*$")
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def holds_floor(text: str) -> str | None:
    """Why the last words say more is coming, or None when they could end a turn.

    Holding cues (Gravano & Hirschberg 2011): a filled pause, a cut-off word, a
    conjunction, preposition or article with nothing after it. A question or an
    exclamation mark from the transcriber closes the sentence; a period does not
    (it writes one at every pause)."""
    t = (text or "").replace("’", "'").strip()
    if not t:
        return None
    words = _WORD.findall(t)
    if not words:
        return None
    last = words[-1]
    low = last.lower()
    if low in FILLERS:
        return "filler"
    if _CUT_MARK.search(t):
        return "cut_off"
    if t.endswith(("?", "!")):
        return None
    if low == "a" and last == "a":          # "plan A" is a name, "a" is an article
        return "dangling"
    if low in DANGLING:
        if low == "so" and len(words) > 1 and words[-2].lower() in _SO_COMPLETE:
            return None
        return "dangling"
    if low in CUT_CONTRACTIONS:
        return "cut_off"
    return None


class EndOfTurnSignal:
    """The transcriber's end-of-turn forecast, as it arrives: the inactivity
    probability on one horizon, and how many steps in a row (within this
    pause) it has held at or above the threshold."""

    def __init__(self, horizon_s: float = EOT_HORIZON_S, threshold: float = EOT_THRESHOLD,
                 clock=time.monotonic):
        self.horizon_s = horizon_s
        self.threshold = threshold
        self.clock = clock
        self.prob: float | None = None
        self.at: float | None = None
        self._recent: deque = deque(maxlen=64)   # (received, probability)

    def update(self, vad) -> float | None:
        if not vad:
            return None
        if any(isinstance(e, dict) and "horizon_s" in e for e in vad):
            entry = min((e for e in vad if isinstance(e, dict)),
                        key=lambda e: abs(float(e.get("horizon_s", 1e9)) - self.horizon_s))
        else:                                  # no horizons named: index 2 is the 2 s one
            entry = vad[min(2, len(vad) - 1)]
        prob = entry.get("inactivity_prob") if isinstance(entry, dict) else None
        if prob is None:
            return None
        self.prob = float(prob)
        self.at = self.clock()
        self._recent.append((self.at, self.prob))
        return self.prob

    def reading(self, since: float | None = None) -> tuple[float | None, int]:
        """(probability, steps in a row at or above threshold since `since`),
        or (None, 0) when no step arrived after `since` or the last is stale."""
        now = self.clock()
        if self.at is None or now - self.at > SIGNAL_STALE_SECS:
            return None, 0
        if since is not None and self.at < since:
            return None, 0
        run = 0
        for at, prob in reversed(self._recent):
            if (since is not None and at < since) or prob < self.threshold:
                break
            run += 1
        return self.prob, run


class ForecastGradiumSTTService(GradiumSTTService):
    """The transcriber, reading its end-of-turn forecast without handing it the
    turn. Pipecat drops "step" messages unless its own turn detection is on,
    which would also stop the flush at each local pause; this keeps the flushes
    and records every step in `self.forecast`."""

    def __init__(self, *args, forecast: EndOfTurnSignal | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.forecast = forecast or EndOfTurnSignal()

    async def _receive_messages(self):
        if self._enable_turn_detection:     # pipecat's own turn detection: its loop
            await super()._receive_messages()
            return
        async for message in self._get_websocket():
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {message}")
                continue
            await self._on_message(msg)

    async def _on_message(self, msg: dict):
        """The parent's handling with turn detection off, plus the forecast."""
        type_ = msg.get("type", "")
        if type_ == "step":
            self.forecast.update(msg.get("vad") or [])
        elif type_ == "text":
            await self._handle_text(msg["text"])
        elif type_ == "flushed":
            await self._handle_flushed()
        elif type_ == "end_of_stream":
            logger.debug("Received end_of_stream message from server")
        elif type_ == "error":
            await self.push_error(error_msg=f"Error: {msg}")


def decide(text: str, silence: float, forecast: tuple[float | None, int]) -> str | None:
    """The reason the turn ends now, or None to keep listening.

    `silence` is seconds since his last speech; `forecast` is
    EndOfTurnSignal.reading() for this pause."""
    steps, hold_secs, fallback_secs, no_signal_secs = EOT_STEPS, HOLD_SECS, FALLBACK_SECS, NO_SIGNAL_SECS
    if not (text or "").strip():
        return None
    hold = holds_floor(text)
    if hold:
        return f"hold_{hold}_timeout" if silence >= hold_secs else None
    prob, run = forecast
    if prob is None:
        return "silence_no_forecast" if silence >= no_signal_secs else None
    if run >= steps:
        return "semantic"
    return "fallback_silence" if silence >= fallback_secs else None


class ForecastTurnStopStrategy(BaseUserTurnStopStrategy):
    """Ends the user turn on the transcriber's forecast, never on a short
    silence after words that hold the floor. See the module docstring."""

    def __init__(self, forecast: EndOfTurnSignal, *, clock=time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self.forecast = forecast
        self.clock = clock
        self._text = ""
        self._speaking = False
        self._speech_end: float | None = None
        self._interim_after_final = False
        self._task: asyncio.Task | None = None

    async def handle_user_turn_started(self):
        await self._reset(clear_speaking=False)

    async def handle_user_turn_stopped(self):
        await self._reset(clear_speaking=True)

    async def _reset(self, *, clear_speaking: bool):
        self._text = ""
        self._speech_end = None
        self._interim_after_final = False
        if clear_speaking:
            self._speaking = False
        await self._stop_watch()

    async def cleanup(self):
        await super().cleanup()
        await self._stop_watch()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._speaking = True
            self._speech_end = None
            await self._stop_watch()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speaking = False
            self._speech_end = self.clock() - float(getattr(frame, "stop_secs", 0.0) or 0.0)
            self._start_watch()
        elif isinstance(frame, TranscriptionFrame):
            self._text = (self._text + " " + (frame.text or "")).strip()
            self._interim_after_final = False
            if not self._speaking and self._speech_end is None:
                # Text with no VAD stop seen: measure the pause from the text.
                self._speech_end = self.clock()
                self._start_watch()
        elif isinstance(frame, InterimTranscriptionFrame):
            self._interim_after_final = True
        return ProcessFrameResult.CONTINUE

    def verdict(self) -> str | None:
        """One evaluation of the turn at this instant (the watch loop's tick)."""
        if self._speaking or self._speech_end is None:
            return None
        silence = self.clock() - self._speech_end
        if self._interim_after_final and silence < FALLBACK_SECS:
            return None                         # the transcriber is still delivering words
        return decide(self._text, silence, self.forecast.reading(since=self._speech_end))

    async def evaluate(self) -> str | None:
        reason = self.verdict()
        if reason:
            silence = self.clock() - (self._speech_end or self.clock())
            prob, run = self.forecast.reading(since=self._speech_end)
            logger.info(f"end of turn: {reason} after {silence:.2f} s of silence"
                        f" (forecast {'none' if prob is None else f'{prob:.2f} x{run}'}): {self._text[-80:]!r}")
            await self.trigger_user_turn_stopped()
        return reason

    def _start_watch(self):
        if self._task and not self._task.done():
            return
        self._task = self.task_manager.create_task(self._watch(), f"{self}::_watch")

    async def _watch(self):
        try:
            while True:
                await asyncio.sleep(TICK_SECS)
                if self._speaking or self._speech_end is None:
                    return
                if await self.evaluate():
                    return
        except asyncio.CancelledError:
            return

    async def _stop_watch(self):
        task, self._task = self._task, None
        if task and not task.done() and task is not asyncio.current_task():
            await self.task_manager.cancel_task(task)
