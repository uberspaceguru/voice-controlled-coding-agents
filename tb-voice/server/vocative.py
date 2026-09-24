"""Who a turn is addressed to, by name, before any model is asked.

`names_the_manager` (manager.py) answers for the manager's own name. This
answers for an AGENT's: "Director, ship the fix" is a message for Director,
and the words after the name are the message (23 Sep, right-hands). Pure, so
a drill can run it without the pipeline: drills/vocative_drill.py.

The transcriber writes names the way it hears them. "Yobi1" arrives as "Yobi
one", "Sys-3PO" as "Sys three P O", so both sides are folded to letters only
with digits spelled out before they are compared. A name matches when the
turn's opening words, folded, equal the folded name — one to four words, the
longest name first so "Director" never steals "Director Two".
"""

import re

_DIGITS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
           "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}


def fold(text: str) -> str:
    """Letters only, lower-case, digits spelled out: 'Sys-3PO' → 'systhreepo',
    'Yobi one' → 'yobione'."""
    out = []
    for ch in text.lower():
        if ch.isdigit():
            out.append(_DIGITS[ch])
        elif ch.isalpha():
            out.append(ch)
    return "".join(out)


def names_an_agent(text: str, names: list[str]) -> tuple[str, str] | None:
    """The agent the turn opens by naming, and the rest of the turn.

    Returns (name, remainder) or None. The name must be the opening of the
    turn — a vocative, like the manager's own — and be followed by a pause the
    transcriber wrote as punctuation, or by nothing: 'Director, run the tests'
    and 'Director.' match; 'the director said no' does not."""
    words = text.split()
    if not words or not names:
        return None
    # Longest fold first, so a name that is a prefix of another cannot win.
    ranked = sorted(((fold(n), n) for n in names if fold(n)), key=lambda p: -len(p[0]))
    for take in range(min(4, len(words)), 0, -1):
        head = " ".join(words[:take])
        # The opening has to END the vocative: a comma, a period, a colon, or
        # the end of the turn. Without this 'Director' would match the first
        # word of 'director of engineering wants a demo'.
        stripped = head.rstrip(".,;:!?")
        ended = stripped != head or take == len(words)
        if not ended:
            continue
        key = fold(stripped)
        for folded, name in ranked:
            if key == folded:
                rest = " ".join(words[take:]).strip()
                rest = re.sub(r"^[,;:.\s]+", "", rest)
                return name, rest
    return None
