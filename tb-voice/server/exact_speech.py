"""An internal speech frame; model text and public tools cannot select this mode."""

from dataclasses import dataclass

from pipecat.frames.frames import TTSSpeakFrame

from exact_values import ExactValue


@dataclass
class ExactSpeakFrame(TTSSpeakFrame):
    value: ExactValue | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.value is None or self.text != self.value.value:
            raise ValueError("Exact speech must carry the validated recorded value")
