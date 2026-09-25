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

4. Each right-hand is talked to by name (25 Sep): "Yobi1, …", "Sys-3PO, …"
   go to that hand's own brain (its `ask` in right-hands.json), and
   "Director, …" to Director. In Tranquility Base Director (the host sets
   TB_RIGHT_HAND_CARDS) the turn is handed to the app, which speaks the answer
   on the hand's own card. A hand with no session is a placeholder and says so.

5. Hands-free in Tranquility Base Director talks to DIRECTOR by default (25 Sep,
   Ahmed: "talking to you conversationally, kind of like how Hands-Free works,
   as you're monitoring all the agents"). The host sets
   TB_DEFAULT_INTERLOCUTOR=director; then every utterance is Director's
   (`route_default`), in one thread for the whole session so "yes" and "the
   second one" bind, and a hand's name at the start ("Yobi1, …") switches the
   interlocutor for that utterance only. A bare "stop" still stops the voice.

`route` is pure, so tests/test_director_link.py runs without the pipeline.
"""

import json
import os
import re
import shutil


THREAD = os.getenv("TB_DIRECTOR_THREAD", "tranquility:voice")


def _roster_path() -> str:
    """TB_RIGHT_HANDS, else the host app's own folder (the Director app hands
    its folder over as VOICE_DISPATCH_SUPPORT_DIR), else Prod's."""
    if os.getenv("TB_RIGHT_HANDS"):
        return os.path.expanduser(os.environ["TB_RIGHT_HANDS"])
    if os.getenv("VOICE_DISPATCH_SUPPORT_DIR"):
        return os.path.join(os.path.expanduser(os.environ["VOICE_DISPATCH_SUPPORT_DIR"]), "right-hands.json")
    return os.path.expanduser("~/Library/Application Support/VoiceDispatch/right-hands.json")


ROSTER = _roster_path()

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


# A right-hand's name as the transcriber writes it, reduced by `_key` (lower
# case, number words as digits, nothing but letters and digits): "Yobi one",
# "Yobi-1" -> "yobi1"; "Sys three P O", "C-3PO", "S3PO" -> the sys3po pattern.
_NUMBERS = {"one": "1", "won": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
_ALIASES = {
    "yobi1": r"y[oa]b+(?:i|y|ee|ie|e)?1",
    "sys3po": r"(?:s[iy]s(?:tem)?|see|sea|c|s)3p(?:o|0|oh|eo)",
    "teamchatmanager": r"teamchat(?:manager)?",
}
_OPENER = re.compile(r"(?i)^\s*(?:(?:hey|hi|ok(?:ay)?|yo)\s+)?(?:the\s+)?")
_STOP = re.compile(r"\s*[,.:;!?-]+\s*")


def _key(words: str) -> str:
    return "".join(_NUMBERS.get(w, w) for w in re.findall(r"[a-z0-9]+", (words or "").lower()))


def _matches(name: str, spoken: str) -> bool:
    key = _key(name)
    return bool(key) and re.fullmatch(_ALIASES.get(key, re.escape(key)), _key(spoken)) is not None


def _named_hand(t: str, names: list[str]) -> tuple[str, str] | None:
    """(hand name, the rest) when the turn opens by naming a right-hand other
    than Director, else None. Up to four words of name; a stop after it, or no
    stop at all (these names are not English words: "Yobi one what's on").
    """
    body = t[_OPENER.match(t).end():]
    others = [n for n in names if _key(n) != "director"]
    stop = _STOP.search(body)
    if stop and len(body[:stop.start()].split()) <= 4:
        head, rest = body[:stop.start()], body[stop.end():]
        for n in others:
            if _matches(n, head):
                return n, rest.strip()
    words = body.split()
    for k in range(min(4, len(words)), 0, -1):
        for n in others:
            if _matches(n, " ".join(words[:k])):
                return n, " ".join(words[k:]).lstrip(",.:;!?- ").strip()
    return None


_MUTE = re.compile(r"(?i)^\s*(?:ok(?:ay)?[,\s]+)?(?:stop|quiet|be quiet|shut up|hush|pause|enough|that's enough|"
                   r"hold on|never ?mind|cancel)\s*[.!]*\s*$")


def director_default() -> bool:
    """The host talks to Director by default (the Director app)."""
    return os.getenv("TB_DEFAULT_INTERLOCUTOR", "").strip().lower() == "director"


def route_default(text: str, names: list[str] | None = None) -> tuple[str, ...] | None:
    """Director's hands-free: every utterance is Director's unless it opens with
    another hand's name, or is only "stop". None for an empty turn."""
    t = (text or "").strip()
    if not t or not re.search(r"[A-Za-z0-9]", t):
        return None
    if _MUTE.match(t):
        return ("mute", "")
    named = _named_hand(t, right_hands() if names is None else names)
    if named:
        return ("hand", named[0], named[1] or "what needs me?")
    m = _VOCATIVE.match(t)
    if m:
        return ("ask", t[m.end():].strip() or "what needs me?")
    return ("ask", t)


def session_thread(path: str | None = None) -> str:
    """The one conversation id for a hands-free session. In the Director app it
    is the Director card's own thread (its session id), so what the card
    showed and what the voice says are one numbered list; else THREAD."""
    if os.getenv("TB_DIRECTOR_THREAD"):
        return os.environ["TB_DIRECTOR_THREAD"]
    if cards_host():
        hand = hand_named("Director", path)
        if hand and hand.get("session"):
            return hand["session"]
    return THREAD


def route(text: str, names: list[str] | None = None) -> tuple[str, ...] | None:
    """What to do with a turn, or None to leave it to the dialogue policy.

    ("ask", words) sends `words` to Director; ("hand", name, words) sends them
    to the right-hand of that name; ("identity", "") answers who the voice is.
    The vocative is kept out of a hand's words and kept in Director's: Director's
    own router understands "Director, what needs me?". `names` defaults to the
    roster's.
    """
    t = (text or "").strip()
    if not t:
        return None
    if _IDENTITY.search(t):
        return ("identity", "")
    named = _named_hand(t, right_hands() if names is None else names)
    if named:
        return ("hand", named[0], named[1] or "what needs me?")
    m = _VOCATIVE.match(t)
    if m:
        rest = t[m.end():].strip()
        return ("ask", rest or "what needs me?")
    if _FOR_DIRECTOR.search(t) or (_TELL.match(t) and not _CONTROL.search(t)):
        return ("ask", t)
    return None


IDENTITY_LINE = ("I'm Tranquility, your voice here. Talk to each right-hand by name: say "
                 "Director, Yobi1 or Sys-3PO, then what you need.")


def director_bin() -> str:
    """The director command: $DIRECTOR_BIN, else PATH, else ~/.local/bin."""
    return (os.getenv("DIRECTOR_BIN") or shutil.which("director")
            or os.path.expanduser("~/.local/bin/director"))


def ask_argv(text: str, thread: str | None = None) -> list[str]:
    return [director_bin(), "ask", text, "--channel", "tranquility", "--external-id", thread or session_thread()]


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


def hands(path: str | None = None) -> list[dict]:
    try:
        with open(path or ROSTER) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = data.get("hands", data) if isinstance(data, dict) else data
    return [h for h in rows if isinstance(h, dict) and h.get("name")]


def right_hands(path: str | None = None) -> list[str]:
    return [h["name"] for h in hands(path)]


def hand_named(name: str, path: str | None = None) -> dict | None:
    return next((h for h in hands(path) if h["name"] == name), None)


def hand_argv(hand: dict, text: str) -> list[str] | None:
    """A hand's `ask` template filled, as right-hands.json writes it; the words
    are one argv element. None when the hand has no brain."""
    template = hand.get("ask")
    if not isinstance(template, list) or not template:
        return None
    session = hand.get("session") or ""
    argv = [str(a).replace("{text}", text).replace("{conversation}", session).replace("{session}", session)
            for a in template]
    argv[0] = os.path.expanduser(argv[0])
    if "/" not in argv[0]:
        argv[0] = shutil.which(argv[0]) or os.path.expanduser(f"~/.local/bin/{argv[0]}")
    return argv


def cards_host() -> bool:
    """The host speaks a hand's answer on the hand's card (the Director app)."""
    return os.getenv("TB_RIGHT_HAND_CARDS") == "1"


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
