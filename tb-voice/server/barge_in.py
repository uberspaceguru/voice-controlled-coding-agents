"""Talking over Director: what a word said over its voice means.

Only for a client whose microphone is already free of Director's voice (the
app's WebRTC engine cancels it; see local_rtc.py). Without that, the mic hears
Director and every one of its sentences would interrupt itself, which is why
the stdio path keeps its mute and its gate and never uses this.

While Director speaks, each transcript (interim ones included, so the first
word counts) is sorted into one of four:

    stop        "stop", "wait", "no", "hold on", a name: cut it off at once
    backchannel "mm-hm", "yeah", "ok", "right": keep talking (R9)
    echo        3+ of Director's own words, in order: its voice leaking past
                the canceller; keep talking, log it (R7)
    claim       two or more other words: he is taking the turn, cut it off
    (wait)      one other word so far: hear the next one first

In the quiet nothing changes: one word starts a turn, as Pipecat's
MinWordsUserTurnStartStrategy always did here, and the gate decides whether it
was for Director. Research: research/turn-taking.md R7-R9 (Schegloff 2000 on
continuers; LiveKit's backchannel handling).
"""

import re
import time
from typing import Callable

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)

# Continuers and acknowledgements: a listener's "go on", never a turn. Any run
# of these alone ("oh okay", "yeah yeah") is a backchannel.
BACKCHANNEL = {
    "mm", "mmm", "mhm", "mm-hm", "mm-hmm", "mmhm", "mmhmm", "hm", "hmm", "uh-huh", "uhhuh",
    "ah", "oh", "yeah", "yea", "yep", "yup", "yes", "ok", "okay", "right", "sure", "cool",
    "nice", "great", "alright", "gotcha", "true", "fine", "good", "exactly", "totally", "wow",
}
# Backchannels made of words that are not one on their own ("i", "got").
BACKCHANNEL_PHRASES = ("got it", "i see", "all right", "makes sense", "uh huh", "mm hm", "mm hmm",
                       "fair enough", "sounds good", "that makes sense")
# Words that take the floor on their own (R9): the keyword alone is enough.
STOP_WORDS = {"stop", "wait", "no", "nope", "pause", "quiet", "cancel", "hush", "sorry",
              "actually", "director", "hey", "excuse"}
STOP_PHRASES = ("hold on", "hang on", "shut up", "never mind", "nevermind", "one sec", "one second",
                "just a sec", "stop it", "not that", "that's not", "thats not", "be quiet",
                "that's enough", "thats enough")

_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def is_echo(ws: list[str], speaking_text: str) -> bool:
    """Three or more words that run, in order, inside the line Director is
    saying: its own voice leaking past the canceller, not him. Checked first,
    because its lines contain "no" and "wait" too."""
    if len(ws) < 3 or not speaking_text:
        return False
    return f" {' '.join(ws)} " in f" {' '.join(words(speaking_text))} "


def classify(text: str, *, speaking_text: str = "", extra_names: tuple[str, ...] = ()) -> str:
    """One of stop, backchannel, echo, claim, wait, empty. Pure, so a table of
    utterances can pin it (tests/test_barge_in.py)."""
    ws = words(text)
    if not ws:
        return "empty"
    if is_echo(ws, speaking_text):
        return "echo"
    # One or two words that Director is itself saying ("Director: ...", "No
    # agents ...") could be its own voice: hear one more word before cutting.
    if len(ws) <= 2 and speaking_text and set(ws) <= set(words(speaking_text)):
        return "wait"
    joined = f" {' '.join(ws)} "
    names = [" ".join(words(n)) for n in extra_names if words(n)]
    if (set(ws) & STOP_WORDS) or any(f" {p} " in joined for p in (*STOP_PHRASES, *names)):
        return "stop"
    rest = joined
    for phrase in sorted(BACKCHANNEL_PHRASES, key=len, reverse=True):
        rest = rest.replace(f" {phrase} ", "  ")
    if all(w in BACKCHANNEL for w in rest.split()):
        return "backchannel"
    return "claim" if len(ws) >= 2 else "wait"


def is_hold(text: str) -> bool:
    """A short floor-taking word said over the voice ("stop", "wait", "hold on",
    "no"): Director stops and listens, and nothing is asked of anyone."""
    ws = words(text)
    return 0 < len(ws) <= 3 and classify(text) == "stop"


class BargeInStrategy(MinWordsUserTurnStartStrategy):
    """Starts a user turn (and so interrupts Director) by `classify`.

    `speaking_text` returns the line Director is saying now, for the echo check;
    `names` the right-hands' names, which cut Director off like a stop word.
    `on_verdict(label, text, ms)` hears every decision made over the voice, with
    the milliseconds since the VAD first heard him, for the barge-in log."""

    def __init__(self, *, quiet_words: int = 1,
                 speaking_text: Callable[[], str] = lambda: "",
                 names: Callable[[], tuple[str, ...]] = lambda: (),
                 on_verdict: Callable[[str, str, int | None], None] | None = None,
                 **kwargs):
        super().__init__(min_words=quiet_words, **kwargs)
        self._speaking_text = speaking_text
        self._names = names
        self._on_verdict = on_verdict
        self._onset: float | None = None
        self._last: tuple[str, str] | None = None

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._onset = time.monotonic()
        return await super().process_frame(frame)

    async def _handle_transcription(
        self, frame: TranscriptionFrame | InterimTranscriptionFrame
    ) -> ProcessFrameResult:
        if not self._bot_speaking:
            return await super()._handle_transcription(frame)
        label = classify(frame.text, speaking_text=self._speaking_text(), extra_names=self._names())
        ms = round((time.monotonic() - self._onset) * 1000) if self._onset else None
        if (label, frame.text) != self._last:
            self._last = (label, frame.text)
            logger.info(f"barge-in: {label} {frame.text!r} {ms if ms is not None else '?'} ms after onset "
                        f"({'interim' if isinstance(frame, InterimTranscriptionFrame) else 'final'})")
            if self._on_verdict:
                self._on_verdict(label, frame.text, ms)
        if label in ("stop", "claim"):
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        if label in ("backchannel", "echo") and isinstance(frame, TranscriptionFrame):
            # A final that is only "mm-hm" must not linger in the aggregator and
            # be glued onto his next real sentence.
            await self.trigger_reset_aggregation()
        return ProcessFrameResult.CONTINUE
