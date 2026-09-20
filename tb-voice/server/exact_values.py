"""Read-only exact requests and values. No generated text becomes a literal answer."""

import re
from dataclasses import dataclass

EXACT_INTENTS = {
    "exact_directory": "Asks for the actual working directory or full directory path, not to open or change it",
    "exact_branch": "Asks for the exact branch name, not to switch, create, explain, or summarize a branch",
    "exact_command": "Asks to read the exact previously reported command, not to run it or recommend a new one",
    "exact_identifier": "Asks for this session’s full identifier or session ID, not for an API key or secret",
}

# Deliberately complete requests: trailing actions/quoted instructions do not match.
_PREFIX = r"(?:tranquility[, :]*)?(?:please\s+)?"
_REQUESTS = {
    "directory": r"(?:what (?:directory|folder) are you working in|(?:give|tell|show) me (?:the |your )?(?:full |exact |literal )?(?:working )?directory(?: path)?|what is (?:the |your )?(?:full |exact )?(?:working )?directory(?: path)?)",
    "branch": r"(?:what branch are you (?:on|working on)|(?:give|tell|show) me (?:the |your )?(?:full |exact |literal )?branch(?: name)?|what is (?:the |your )?(?:full |exact )?branch(?: name)?)",
    "command": r"(?:(?:read|give|tell|show)(?: me)? (?:the )?(?:exact|literal|full) (?:last |previous |reported )?command|what (?:was|is) the (?:exact|literal|full) (?:last |previous |reported )?command)",
    "identifier": r"(?:(?:give|tell|show) me (?:the |your |this )?(?:full |exact |literal )?session (?:id|identifier)|what is (?:the |your |this )?(?:full |exact )?session (?:id|identifier))",
}


def exact_request(text: str) -> str | None:
    for kind, pattern in _REQUESTS.items():
        if re.fullmatch(_PREFIX + pattern + r"[?.!\s]*", text.strip(), re.I):
            return kind
    return None


@dataclass(frozen=True)
class ExactValue:
    kind: str
    value: str

    def __post_init__(self):
        if self.kind not in _REQUESTS or not self.value or len(self.value) > 600:
            raise ValueError("Missing or oversized exact value")
        if any(ord(c) < 32 for c in self.value) or re.search(
            r"(?i)(?:api[_ -]?key|password|secret|bearer\s|\b(?:sk|gsk|gc)_[\w-]+)", self.value
        ):
            raise ValueError("Not a speakable exact value")


def recorded_value(kind: str, target: dict, brief: dict) -> ExactValue | None:
    """Directory/session ID are live metadata; branch/command must be labeled facts.

    Never scan the user's question or generated recap/proposal for values. A brief
    mismatch, duplicate label, multiline value, or missing fact fails closed.
    """
    if brief and brief.get("sessionId") != target.get("sessionId"):
        return None
    if kind == "directory":
        value = target.get("cwd")
    elif kind == "identifier":
        value = target.get("sessionId")
    else:
        message = brief.get("lastAssistantMessage") or ""
        # The CLI caps this field at 600 characters. At the boundary we cannot
        # prove the value is complete or that a later label would contradict it.
        if not isinstance(message, str) or len(message) >= 600:
            return None
        label = {"branch": r"branch(?: name)?", "command": r"(?:last |reported )?command"}.get(kind)
        if not label:
            return None
        hits = re.findall(rf"^\s*(?:\*\*)?{label}(?:\*\*)?:\s*(.+?)\s*$", message, re.I | re.M)
        if len(hits) != 1:
            return None
        value = hits[0]
        if value.startswith("`") and value.endswith("`") and value.count("`") == 2:
            value = value[1:-1]
        elif "`" in value:
            return None
        # Labeled prose is not an exact branch name; commands require code delimiters.
        if kind == "branch" and re.search(r"\s", value):
            return None
        if kind == "command" and not hits[0].startswith("`"):
            return None
    if not isinstance(value, str):
        return None
    try:
        return ExactValue(kind, value)
    except ValueError:
        return None
