#!/usr/bin/env python3
"""Hands-free in Tranquility Base Director, without the microphone or speaker.

    .venv/bin/python drills/handsfree_director_drill.py

The host environment the Director app hands its manager (TB_RIGHT_HAND_CARDS=1,
TB_DEFAULT_INTERLOCUTOR=director, its own VOICE_DISPATCH_SUPPORT_DIR), and each
utterance through Manager._dialogue_turn exactly as a transcribed turn goes:
the REAL `director ask` in the session's one thread, the REAL right-hand asks.
Replaced: the voice (every line printed) and the app (an `answer` event is
printed as the line its card would speak). Director's own JSON for each ask is
kept, so the transcript shows its intent and whether it typed anything. Ends
with ten seconds of silence, in which nothing may be said.
"""
import asyncio
import json
import os
import sys
import time
from unittest.mock import AsyncMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

os.environ.setdefault("VOICE_DISPATCH_SUPPORT_DIR",
                      os.path.expanduser("~/Library/Application Support/VoiceDispatch-Director"))
os.environ["TB_RIGHT_HAND_CARDS"] = "1"
os.environ["TB_DEFAULT_INTERLOCUTOR"] = "director"

UTTERANCES = [
    "Director, what needs me?",
    "tell me more about the first one",
    "tell the Wispr worker yes",
    "Yobi1, what's my day?",
]


async def main(utterances):
    from test_memory_manager import make_manager
    import director_link
    import manager
    director_link.ROSTER = director_link._roster_path()
    m = make_manager()
    m._card_secs = lambda reply: 0      # the card's length is the app's; nothing waits here
    heard = []
    real_run = manager._run

    async def run(*argv, timeout=60):
        # Director's ask with --json, to show its intent and receipt; its reply
        # is what the manager receives, exactly as the plain ask prints it.
        if argv[1:2] == ("ask",):
            code, out = await real_run(argv[0], "--json", *argv[1:], timeout=timeout)
            try:
                d = json.loads(out)
            except ValueError:
                return code, out
            print(f"    [director: intent={d.get('intent')} acted={d.get('acted')} "
                  f"receipt={d.get('receipt_id')} thread={d.get('external_id')}]")
            return code, d.get("reply") or ""
        return await real_run(*argv, timeout=timeout)

    async def say(text, voice="manager", **_):
        heard.append(text)
        print(f"  Tranquility: {text}")
        return True

    async def emit(_processor, event, **fields):
        if event == "answer":
            heard.append(fields["text"])
            print(f"  {fields['name']} (on {fields['name']}'s card): {fields['text']}")
        elif event == "tool":
            print(f"    [{fields.get('meaning')}]")

    m._say = say
    with patch("manager.note"), patch("dialogue_manager.emit", AsyncMock()), \
            patch("manager.emit", emit), patch("manager._run", run):
        print(f"thread: {director_link.session_thread()}")
        for u in utterances:
            print(f"you: {u}")
            t0 = time.monotonic()
            await m._dialogue_turn(u, None, None)
            print(f"    ({time.monotonic() - t0:.1f}s)")
        print("you: (silence, 10 s)")
        before = len(heard)
        await asyncio.sleep(10)
        print("  (nothing said)" if len(heard) == before else f"  REPEATED: {heard[before:]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or UTTERANCES))
