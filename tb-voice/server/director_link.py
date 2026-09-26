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
_VOCATIVE = re.compile(r"^\s*(?:(?:hey|hi|ok(?:ay)?)[,\s]+)?(?:the\s+)?director\s*(?:[,.:;!?-]+\s*|$)", re.I)

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
_NUMBERS = {"one": "1", "won": "1", "wan": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
_ALIASES = {
    "yobi1": r"y?[oa]b+(?:i|y|ee|ie|e)?1",      # "Yobi-Wan", measured 25 Sep
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


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _close(name: str, spoken: str) -> bool:
    """Near enough when the name is said as a vocative, with a stop after it:
    the transcriber heard "Yobi one" as "Yodhi1." and "Yobi-Wan." (25 Sep).
    Two edits for a name of five letters or more, none below that."""
    key, heard = _key(name), _key(spoken)
    limit = 3 if len(key) >= 10 else 2 if len(key) >= 5 else 0   # "Adrian Quility" is Tranquility, 3 edits
    return limit > 0 and abs(len(key) - len(heard)) <= limit and _distance(key, heard) <= limit


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

    # A near name, only before a real stop (never a hyphen inside a word).
    vstop = re.search(r"\s*[,.:;!?]+\s*", body)
    if vstop and len(body[:vstop.start()].split()) <= 3:
        for n in others:
            if not any(_matches(m, body[:vstop.start()]) for m in others) and _close(n, body[:vstop.start()]):
                return n, body[vstop.end():].strip()
    words = body.split()
    for k in range(min(4, len(words)), 0, -1):
        for n in others:
            if _matches(n, " ".join(words[:k])):
                return n, " ".join(words[k:]).lstrip(",.:;!?- ").strip()
    return None


_MUTE = re.compile(r"(?i)^\s*(?:ok(?:ay)?[,\s]+)?(?:stop|quiet|be quiet|shut up|hush|pause|enough|that's enough|"
                   r"hold on|never ?mind|cancel)\s*[.!]*\s*$")


# How the transcriber spells Ahmed's names, measured on the real microphone
# (25 Sep): "the Wispr worker" arrives as "the Whisper worker", and Director
# finds nothing open for it. Fixed before any brain hears the words.
_SPELLINGS = [(re.compile(r"(?i)\bwhisper\b"), "Wispr"),
              (re.compile(r"(?i)\byobi[- ]?(?:one|wan|won)\b"), "Yobi1")]


def spoken_fixes(text: str) -> str:
    for pattern, name in _SPELLINGS:
        text = pattern.sub(name, text)
    return text


# A name said on its own is a CALL, not a question: the transcriber splits
# "Director, what needs me?" at the comma into two turns (measured 25 Sep), and
# answering the name alone answered twice. A call is heard with the listening
# cue, and the next utterance within this window goes to the one called.
CALL_WINDOW = 8.0
# A follow-up ("yes", "the second one") is Director's when it comes within this
# many seconds of Director finishing a line. 8 s was measured too short on
# Ahmed's first real conversation (25 Sep 19:40): "I don't remember anything
# about this project", 15 s after Director spoke, never reached it. Inside the
# window Director's own gate (Jev, with the conversation as context) decides
# whether a line was for it and stays silent when it was not.
FOLLOW_UP_SECS = 45.0


# The card's own voice, heard back (25 Sep, real microphone): the mic is muted
# for the answer's estimated length, and a long answer outlasted the estimate;
# its tail ("Would you head to San Jose? California.") came back as a turn and
# went to Director. A turn made mostly of the last answer's words, soon after
# it, is that answer and not Ahmed.
ECHO_SECS = 30.0


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("é", "e"))


def is_echo(text: str, last_answer: str | None, age: float) -> bool:
    heard, said = _words(text), set(_words(last_answer or ""))
    if not heard or not said or age > ECHO_SECS:
        return False
    return len(heard) >= 2 and sum(w in said for w in heard) / len(heard) >= 0.75


# Delay is announced, the way people do it (Clark & Fox Tree 2002: "uh" for a
# short delay, "um" for a long one; Kendrick & Torreira 2015: silence past
# ~700 ms is heard as trouble; a 2025 study: a spoken filler eased 4-6.5 s waits,
# a sound or icon did nothing). Ahmed's first session had 3-5 s of unannounced
# silence per turn and "are you there?" four times in four minutes.
# Revised from the research (research/grounding-memory.md, 25 Sep): a bare "Mm."
# or "um" is an anti-pattern; only a filler tied to the request helped
# (Boukaram 2021; Liu, Guo & Mousas 2026), and repeating "please wait" made a
# wait feel longer (Lopez Gambino 2018). Nothing under ~1.2 s; then his request
# said back in a few words; then where Director is looking; never twice.
# Policy v2 (SPEC-voice-v2 R4, 25 Sep night review): Ahmed heard the old lines as
# "one minute", and one fired on nearly every turn. At most ONE filler per turn,
# only after 1.5 s; none for a hearing check, a count or a yes/no question (the
# answer is short, a filler only delays it); five lines in rotation, never one of
# the last three; no "sec", "minute", "moment" or "please wait" in any of them.
FILLER_AFTER = 1.5
_TOPIC = re.compile(r"(?i)\b(the\s+[\w-]+(?:\s+[\w-]+)?\s+one)\b")
FILLERS = ("Checking.", "Let me look.", "Looking at the fleet.", "Having a look.", "{topic}, looking.")
_NO_FILLER = re.compile(
    r"(?i)(\bcan you hear me\b|\bare you (there|here|listening|alive)\b|\bhello\b|\bhow many\b|\bhow much\b"
    r"|^\W*(is|did|are|does|do|can|could|will|would|was|were|has|have|had|should)\b)")


# A hearing check is answered by code, at once, before any model or filler timer
# (Ahmed, 26 Sep 09:55: "Hello. Director, you there?" got "Let me check that."
# then 1.3 s of model). The words left after the greetings and the names must be
# one of these, or nothing at all when he only said a name or "hello?".
_CHECK_WORDS = {"hello", "hi", "hey", "yo", "director", "tranquility", "tranquillity"}
HEARING_CHECKS = {
    "", "you there", "are you there", "you still there", "are you still there", "still there",
    "can you hear me", "you hear me", "do you hear me", "can you hear me now", "hear me",
    "are you here", "you here", "are you listening", "you listening", "are you awake", "you awake",
    "are you with me", "you with me", "are you alive", "you alive", "anyone there", "is anyone there",
}
HEARING_REPLY = "Yes, I'm here."


def hearing_check(text: str) -> bool:
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words:
        return False
    rest = " ".join(w for w in words if w not in _CHECK_WORDS)
    if rest:
        return rest in HEARING_CHECKS
    # Only greetings and names: "Director?" and "Hello?" are checks; "Director." and "Hey, Director." stay calls
    # (the next words go to Director), as before.
    return "?" in text or set(words) <= {"hello", "hi"}


def wants_filler(words: str) -> bool:
    """No filler for a hearing check, a count or a yes/no question."""
    return not _NO_FILLER.search((words or "").strip())


def filler(words: str, recent: list[str] | tuple = ()) -> str:
    """The next filler in rotation, never one of the last three said."""
    m = _TOPIC.search(words or "")
    lines = [line for line in FILLERS if "{topic}" not in line]
    if m:
        lines.append(f"{m.group(1)[:1].upper()}{m.group(1)[1:]}, looking.")
    last = list(recent)[-3:]
    start = (lines.index(last[-1]) + 1) if last and last[-1] in lines else 0
    for i in range(len(lines)):
        line = lines[(start + i) % len(lines)]
        if line not in last:
            return line
    return lines[start % len(lines)]


# Lookups (Director's promises, lookups.py): polled while hands-free runs; a
# finished one is pre-announced at a pause while a conversation is open, so he
# chooses when to hear it (Ahmed, 25 Sep: "this thing we were talking about is
# now ready; do you want to talk about it whenever you're ready?").
LOOKUP_POLL_S = 4.0
QUIET_BEFORE_ANNOUNCE_S = 1.5


def ready_line(lookup: dict) -> str:
    about = re.split(r"\s+\(agent\s", (lookup.get("about") or lookup.get("question") or "what you asked"))[0].strip()
    about = about.rstrip("?.! ")
    if lookup.get("result"):
        return f"Hey, about {about}: that's ready. Want to go through it now?"
    return f"About {about}: I couldn't find that out. Want to hear what I got?"


_LEADING_STOP = re.compile(r"(?i)^\s*(?:ok(?:ay)?[,\s]+)?(?:stop|wait|hold on|no|sorry)\s*[.,!]+\s*(?=\S)")


def after_stop(text: str) -> str:
    """The turn without a leading "Stop." / "Wait," that barge-in already acted on, when more words follow it."""
    rest = _LEADING_STOP.sub("", text or "", count=1)
    return rest if re.search(r"[A-Za-z]{2}", rest) else text


def director_default() -> bool:
    """The host talks to Director by default (the Director app)."""
    return os.getenv("TB_DEFAULT_INTERLOCUTOR", "").strip().lower() == "director"


# Director answers to its own name and to the voice's: "Tranquility, …" is
# Ahmed talking to the voice, which is Director's (25 Sep). Heard as
# "Adrian Quility" on the real microphone.
DIRECTOR_NAMES = ("Director", "Tranquility")


# How the transcriber writes "Director" when it gets it wrong, before a stop
# (Director's own gate lists the same; "I direct." heard on Ahmed's first
# session, 25 Sep 19:44).
_MISHEARD = re.compile(r"(?i)^\s*(?:(?:hey|hi|ok(?:ay)?)[,\s]+)?(?:i\s+direct|directory|direct(?:\s+her|er|ors?)|"
                       r"the\s+rector|a\s+director)\s*[,.:;!?-]+\s*")


def _director_vocative(t: str) -> str | None:
    """The rest of the turn when it opens by calling Director (or Tranquility),
    exactly or near enough before a real stop; else None."""
    m = (_VOCATIVE.match(t)
         or re.match(r"(?i)^\s*(?:(?:hey|hi|ok(?:ay)?)[,\s]+)?tranquill?ity\s*(?:[,.:;!?-]+\s*|$)", t)
         or _MISHEARD.match(t))
    if m:
        return t[m.end():]
    body = t[_OPENER.match(t).end():]
    stop = re.search(r"\s*[,.:;!?]+\s*", body)
    if stop and len(body[:stop.start()].split()) <= 3 and any(_close(n, body[:stop.start()]) for n in DIRECTOR_NAMES):
        return body[stop.end():]
    return None


def route_default(text: str, names: list[str] | None = None, follow_up: bool = False) -> tuple[str, ...] | None:
    """Director's hands-free, gated (25 Sep, tb-address-gate): a turn is for a
    right-hand only when it opens with a name (a hand's, or Director's, or
    Tranquility's, near names included), or when it is a follow-up (said within
    a few seconds of Director speaking, or of a call). Anything else is the
    room's talk and None: ignored, silently. "Stop" always stops."""
    t = (text or "").strip()
    if not t or not re.search(r"[A-Za-z0-9]", t):
        return None
    if _MUTE.match(t):
        return ("mute", "")
    named = _named_hand(t, right_hands() if names is None else names)
    if named:
        rest = spoken_fixes(named[1]).strip()
        return ("hand", named[0], rest) if re.search(r"[A-Za-z0-9]", rest) else ("call", named[0])
    rest = _director_vocative(t)
    if rest is not None:
        rest = spoken_fixes(rest).strip()
        return ("ask", rest) if re.search(r"[A-Za-z0-9]", rest) else ("call", "Director")
    # What only Director is for opens a conversation without the name:
    # "what needs me?", "what's ready", "catch me up".
    if follow_up or _FOR_DIRECTOR.search(t):
        return ("ask", spoken_fixes(t))
    return None


def answer_call(routed: tuple, called: tuple | None, now: float) -> tuple:
    """The utterance after a call goes to the one called, unless it names
    someone itself. `called` is (name, when)."""
    if called and routed and routed[0] == "ask" and now - called[1] < CALL_WINDOW:
        return ("hand", called[0], routed[1]) if called[0] != "Director" else routed
    return routed


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


def ask_argv(text: str, thread: str | None = None, named: bool = False) -> list[str]:
    # --json: Director says what the turn was, not only what to say (close, incomplete; 26 Sep).
    # --named: this voice heard his name for Director and took it off the words, so Director must not judge the
    # bare words again ("can you hear me?" alone scored 0.28 and was dropped, 25 Sep 22:45).
    return ([director_bin(), "--json", "ask", text] + (["--named"] if named else [])
            + ["--channel", "tranquility", "--external-id", thread or session_thread()])


def named_director(text: str) -> bool:
    """The turn opens by calling Director (or Tranquility, or a known mishearing of either)."""
    return _director_vocative((text or "").strip()) is not None


# A half sentence Director held (its "complete" judgment) is joined to what he
# says next within this many seconds (research/speech-acts.md Q6).
HOLD_FRAGMENT_SECS = 6.0


def director_reply(out: str) -> tuple[str, dict]:
    """(the words to speak, Director's flags) from `director --json ask`; plain text is taken as the words."""
    try:
        d = json.loads(out)
    except (ValueError, TypeError):
        return out, {}
    if isinstance(d, dict) and "reply" in d:
        return d.get("reply") or "", d
    return out, {}


CARD_KINDS = ("list", "item", "action", "screen")


def card_payload(flags: dict) -> str:
    """What Director's card shows with this turn (M26), as the JSON string the
    app's `card` event carries: Director's `card_json` (an object, or that
    object as a string) when it is one of list, item, action or screen, else
    "" so the card drops the last one. Its `card` key is the sentence, not this."""
    raw = (flags or {}).get("card_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return ""
    if not isinstance(raw, dict) or raw.get("kind") not in CARD_KINDS:
        return ""
    return json.dumps(raw, separators=(",", ":"))


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
