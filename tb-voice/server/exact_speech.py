"""An internal speech frame; model text and public tools cannot select this mode."""

from collections.abc import Callable
from dataclasses import dataclass, field

from pipecat.frames.frames import TTSSpeakFrame

from exact_values import ExactValue


@dataclass
class DialogueSpeakFrame(TTSSpeakFrame):
    current: Callable[[], bool] | None = field(default=None, repr=False)
    response_mode: str = "summary"
    delivery: object | None = field(default=None, repr=False)


@dataclass
class ExactSpeakFrame(DialogueSpeakFrame):
    value: ExactValue | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.value is None or self.text != self.value.value:
            raise ValueError("Exact speech must carry the validated recorded value")
