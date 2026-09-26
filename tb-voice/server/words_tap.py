"""Tell the manager when words (not just sound) are arriving from the mic.

A finished reply waits while Ahmed is talking, but VAD alone cannot tell him
from the room: on 25 Sep (18:19) a reply sat ready for 27.6 s behind room sound
while the manager showed "hearing", and he said "Hello?" into the gap. The
transcriber's words are the evidence that he is talking; this passes every
frame through unchanged and reports each transcript to the manager.
"""

from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class WordsTap(FrameProcessor):
    def __init__(self, manager, **kwargs):
        super().__init__(**kwargs)
        self._manager = manager

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (direction == FrameDirection.DOWNSTREAM
                and isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
                and (frame.text or "").strip()):
            self._manager.words_heard(frame.text)
        await self.push_frame(frame, direction)
