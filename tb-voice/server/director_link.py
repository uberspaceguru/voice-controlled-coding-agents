"""Director, through the voice (24 Sep 2026).

Director is the program that runs Ahmed's agents: `director ask` answers what he
says, `director --json status` knows the fleet. This manager is Tranquility, the
voice; it is not the fleet manager and it is not Director. So:

1. A turn addressed to Director ("Director, what needs me?"), a question about
   what needs him or what is ready, and an instruction to tell an agent
   something are Director's. They go to
   `director ask "<text>" --channel tranquility --external-id <thread>` and the
   line Director returns is spoken as it came back, prefixed "Director:" so the
   ear knows who is talking.
2. Asked who it is, the voice says it is Tranquility, and that Director answers
   through it.
3. The fleet it describes is Director's: the right-hands from
   right-hands.json and the counts from `director --json status`, never its own
   list of every live process.

`route` is pure, so tests/test_director_link.py runs without the pipeline.
"""

import json
import os
import re
import shutil


THREAD = os.getenv("TB_DIRECTOR_THREAD", "tranquility:voice")
ROSTER = os.path.expanduser(os.getenv(
    "TB_RIGHT_HANDS", "~/Library/Application Support/VoiceDispatch/right-hands.json"))

# The vocative: the turn opens with Director's name. The transcriber writes it
# several ways; all of them are the name when they open the turn.
# The name must END the vocative (a comma, a stop, or the end of the turn):
# "the director of engineering said no" is not addressed to Director.
_VOCATIVE = re.compile(r"^\s*(?:hey\s+|ok(?:ay)?\s+)?(?:the\s+)?director\s*(?:[,.:;!?-]+\s*|$)", re.I)

# What Director is for, said without its name.
_FOR_DIRECTOR = re.compile(
    r"(?i)\b(what (?:needs|need) me|what needs (?:my|your) (?:attention|answer)|what(?:'s| is) (?:ready|waiting on me|"
    r"waiting for me|blocked on me)|anything (?:ready|for me|need(?:s)? me)|what do (?:you|i) need from me|"
    r"what should i (?:look at|do next)|catch me up|what(?:'s| is| are) (?:the )?things? (?:that )?need(?:s)? me)\b")

# "Tell the Wispr worker yes", "ask Yobi1 to …", "let S3PO know …". Not a stop:
# "ask Alpha to stop its task" is this manager's own hard stop control, which
# interrupts the agent; a message typed into it would only ask.
_TELL = re.compile(r"(?i)^\s*(?:please\s+)?(tell|ask|remind|let|nudge|ping)\s+\S+")
_CONTROL = re.compile(r"(?i)\b(stop|cancel|interrupt|halt|abort|kill|end)\b")

# Questions about who the voice is.
_IDENTITY = re.compile(
    r"(?i)\b(are you (?:the )?director|who are you|what are you|are you the fleet manager|who am i talking to)\b")


def route(text: str) -> tuple[str, str] | None:
    """What to do with a turn, or None to leave it to the dialogue policy.

    ("ask", words) sends `words` to Director; ("identity", "") answers who the
    voice is. The vocative is kept in the words: Director's own router
    understands "Director, what needs me?" and keeps the thread's items bound.
    """
    t = (text or "").strip()
    if not t:
        return None
    if _IDENTITY.search(t):
        return ("identity", "")
    m = _VOCATIVE.match(t)
    if m:
        rest = t[m.end():].strip()
        return ("ask", rest or "what needs me?")
    if _FOR_DIRECTOR.search(t) or (_TELL.match(t) and not _CONTROL.search(t)):
        return ("ask", t)
    return None


IDENTITY_LINE = ("I'm Tranquility, your voice here. Director runs your agents and answers "
                 "through me: say Director, then what you need.")


def director_bin() -> str:
    """The director command: $DIRECTOR_BIN, else PATH, else ~/.local/bin."""
    return (os.getenv("DIRECTOR_BIN") or shutil.which("director")
            or os.path.expanduser("~/.local/bin/director"))


def ask_argv(text: str, thread: str = THREAD) -> list[str]:
    return [director_bin(), "ask", text, "--channel", "tranquility", "--external-id", thread]


def flatten(reply: str) -> str:
    """Director's reply as one spoken line: its own words in its own order,
    newlines and list numbers turned into sentence breaks. Nothing reworded."""
    lines = [ln.strip() for ln in (reply or "").splitlines() if ln.strip()]
    out = []
    for ln in lines:
        ln = re.sub(r"^(\d+)\.\s+", r"\1: ", ln)          # "1. w-a17: …" -> "1: w-a17: …"
        out.append(ln if ln[-1] in ".?!:" else ln + ".")
    return " ".join(out)


def chunks(line: str, max_words: int = 70) -> list[str]:
    """Split a long line at sentence ends into parts the speaker will not cut."""
    parts, cur = [], []
    for sentence in re.split(r"(?<=[.?!])\s+", line):
        words = sentence.split()
        if cur and len(cur) + len(words) > max_words:
            parts.append(" ".join(cur))
            cur = []
        cur += words
    if cur:
        parts.append(" ".join(cur))
    return parts


def right_hands(path: str = ROSTER) -> list[str]:
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return []
    hands = data.get("hands", data) if isinstance(data, dict) else data
    return [h.get("name") for h in hands if isinstance(h, dict) and h.get("name")]


def inventory(status: dict, hands: list[str]) -> str:
    """The fleet as Director sees it: the right-hands by name, then the counts."""
    groups = status.get("groups") or {}
    counts = []
    for key, word in (("needs_you", "need you"), ("working", "working"),
                      ("stuck", "stuck"), ("idle", "idle")):
        n = len(groups.get(key) or [])
        if n:
            counts.append(f"{n} {'needs you' if key == 'needs_you' and n == 1 else word}")
    line = ""
    if hands:
        line = "Your right-hands are " + (", ".join(hands[:-1]) + " and " + hands[-1] if len(hands) > 1 else hands[0]) + "."
    if counts:
        line += " Director is tracking the rest: " + ", ".join(counts) + "."
    return line.strip() or "Director has nothing to report."
