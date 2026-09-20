"""Correlated transport-observable speech delivery, never evidence of human hearing.

Pipecat's output transport forwards audio only after its write succeeds. It
re-chunks audio without retaining context IDs, so the observer binds those chunks
to ordered TTSStarted/TTSStopped markers for each transport destination. Ambiguous
ordering, missing audio, interruptions, and timeouts fail closed.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

TERMINAL = {"output_complete", "interrupted", "failed", "unknown"}


def audio_seconds(frame) -> float:
    rate, channels = getattr(frame, "sample_rate", 0), getattr(frame, "num_channels", 0)
    return len(frame.audio) / (2 * rate * channels) if rate > 0 and channels > 0 else 0.0


@dataclass
class Delivery:
    id: str
    text: str
    context_id: str | None = None
    status: str = "queued"
    reason: str = "queued"
    generated_text: str = ""
    generated_frames: int = 0
    generated_seconds: float = 0.0
    output_frames: int = 0
    output_seconds: float = 0.0
    output_started: bool = False
    output_stopped: bool = False
    synthesis_complete: bool = False
    provider_end_received: bool = False
    created: float = field(default_factory=time.monotonic)
    completed: float | None = None
    current: Callable[[], bool] | None = field(default=None, repr=False)
    completion: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _generated_ids: set[int] = field(default_factory=set, repr=False)
    _output_ids: set[int] = field(default_factory=set, repr=False)


class DeliveryBook:
    def __init__(self, on_change=None, limit=256):
        self.records: dict[str, Delivery] = {}
        self.contexts: dict[str, Delivery] = {}
        self.limit = limit
        self.on_change = on_change

    def _event(self, delivery):
        if self.on_change:
            self.on_change(delivery)
            return
        from events import line
        line("speech_delivery", delivery=delivery.id, context=delivery.context_id,
             status=delivery.status, reason=delivery.reason,
             generated_frames=delivery.generated_frames,
             generated_seconds=round(delivery.generated_seconds, 4),
             output_frames=delivery.output_frames,
             output_seconds=round(delivery.output_seconds, 4))

    def create(self, text: str, *, current=None) -> Delivery:
        while len(self.records) >= self.limit:
            old = next((r for r in self.records.values() if r.status in TERMINAL), None)
            if old is None:
                delivery = Delivery(uuid.uuid4().hex, text, current=current)
                self.finish(delivery, "failed", "delivery_capacity")
                return delivery
            self.records.pop(old.id)
            if old.context_id and self.contexts.get(old.context_id) is old:
                self.contexts.pop(old.context_id)
        delivery = Delivery(uuid.uuid4().hex, text, current=current)
        self.records[delivery.id] = delivery
        self._event(delivery)
        return delivery

    def finish(self, delivery: Delivery | None, status: str, reason: str) -> bool:
        if delivery is None or delivery.status in TERMINAL:
            return False
        if status not in TERMINAL:
            raise ValueError("Only terminal delivery states can finish output")
        delivery.status, delivery.reason = status, reason
        delivery.completed = time.monotonic()
        delivery.completion.set()
        self._event(delivery)
        return True

    def valid(self, delivery) -> bool:
        if delivery is None or delivery.status in TERMINAL:
            return False
        if delivery.current and not delivery.current():
            self.finish(delivery, "interrupted", "superseded")
            return False
        return True

    def bind(self, delivery, context_id, generated_text="") -> bool:
        if not self.valid(delivery):
            return False
        if (not context_id or (delivery.context_id and delivery.context_id != context_id)
                or (context_id in self.contexts and self.contexts[context_id] is not delivery)):
            self.finish(delivery, "unknown", "context_binding_conflict")
            return False
        delivery.context_id = context_id
        if generated_text:
            delivery.generated_text = generated_text
        delivery.status, delivery.reason = "synthesizing", "provider_request"
        self.contexts[context_id] = delivery
        self._event(delivery)
        return True

    def for_context(self, context_id):
        return self.contexts.get(context_id)

    def generated_audio(self, context_id, frame):
        delivery = self.for_context(context_id)
        if not self.valid(delivery) or frame.id in delivery._generated_ids:
            return
        delivery._generated_ids.add(frame.id)
        delivery.generated_frames += 1
        delivery.generated_seconds += audio_seconds(frame)

    def provider_end(self, context_id):
        delivery = self.for_context(context_id)
        if self.valid(delivery):
            delivery.provider_end_received = True

    def generation_complete(self, context_id):
        delivery = self.for_context(context_id)
        if self.valid(delivery):
            if not delivery.provider_end_received:
                # A framework queue timeout also calls context-completed and
                # emits a stop. It cannot prove the utterance finished synthesis.
                self.finish(delivery, "unknown", "provider_end_not_observed")
                return
            delivery.synthesis_complete = True
            self._evaluate(delivery)

    def output_start(self, context_id):
        delivery = self.for_context(context_id)
        if self.valid(delivery):
            delivery.output_started = True

    def output_audio(self, context_id, frame):
        delivery = self.for_context(context_id)
        if not self.valid(delivery) or frame.id in delivery._output_ids:
            return
        if not delivery.output_started:
            self.finish(delivery, "unknown", "audio_without_start")
            return
        delivery._output_ids.add(frame.id)
        delivery.output_frames += 1
        delivery.output_seconds += audio_seconds(frame)

    def output_stop(self, context_id):
        delivery = self.for_context(context_id)
        if self.valid(delivery):
            delivery.output_stopped = True
            self._evaluate(delivery)

    def _evaluate(self, delivery):
        if not self.valid(delivery) or not (delivery.synthesis_complete and delivery.output_stopped):
            return
        if not delivery.output_started:
            self.finish(delivery, "unknown", "stop_without_start")
        elif not delivery.generated_frames or delivery.generated_seconds <= 0:
            self.finish(delivery, "failed", "no_generated_audio")
        elif not delivery.output_frames or delivery.output_seconds <= 0:
            self.finish(delivery, "failed", "no_successful_output_audio")
        elif delivery.output_seconds + 0.0001 < delivery.generated_seconds:
            # A tenth of a millisecond covers sample rounding, not a lost audio
            # chunk. Output padding may make output slightly longer than input.
            self.finish(delivery, "failed", "incomplete_output_audio")
        else:
            self.finish(delivery, "output_complete", "transport_output_complete")

    def interrupt(self, reason="interruption"):
        for delivery in list(self.records.values()):
            self.finish(delivery, "interrupted", reason)

    async def wait(self, delivery, timeout=12.0) -> bool:
        try:
            await asyncio.wait_for(delivery.completion.wait(), timeout)
        except TimeoutError:
            self.finish(delivery, "unknown", "output_timeout")
        return delivery.status == "output_complete"


class OutputDeliveryObserver(FrameProcessor):
    """Place immediately AFTER transport.output(), before assistant aggregation."""

    def __init__(self, book: DeliveryBook, **kwargs):
        super().__init__(**kwargs)
        self.book = book
        self._active: dict[str | None, str] = {}

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        destination = getattr(frame, "transport_destination", None)
        context = getattr(frame, "context_id", None)
        if isinstance(frame, (InterruptionFrame, CancelFrame)):
            self.book.interrupt(type(frame).__name__)
            self._active.clear()
        elif isinstance(frame, EndFrame):
            for delivery in list(self.book.records.values()):
                self.book.finish(delivery, "unknown", "output_ended")
            self._active.clear()
        elif isinstance(frame, ErrorFrame):
            for context_id in self._active.values():
                self.book.finish(self.book.for_context(context_id), "failed", "output_error")
        elif isinstance(frame, TTSStartedFrame):
            old = self._active.get(destination)
            if old and old != context:
                self.book.finish(self.book.for_context(old), "unknown", "overlapping_output_contexts")
                self.book.finish(self.book.for_context(context), "unknown", "overlapping_output_contexts")
            if context:
                self._active[destination] = context
                self.book.output_start(context)
            else:
                self._active.pop(destination, None)
        elif isinstance(frame, TTSAudioRawFrame):
            active = self._active.get(destination)
            if context and context != active:
                self.book.finish(self.book.for_context(context), "unknown", "audio_context_mismatch")
            elif active:
                self.book.output_audio(active, frame)
        elif isinstance(frame, TTSStoppedFrame):
            if context and self._active.get(destination) == context:
                self.book.output_stop(context)
                self._active.pop(destination, None)
            elif context:
                # An old/foreign stop never completes whichever utterance is
                # current; it may only settle its own identifiable record.
                self.book.finish(self.book.for_context(context), "unknown", "stop_context_mismatch")
        await self.push_frame(frame, direction)
