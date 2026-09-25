#!/usr/bin/env python3
"""A real Director turn through the manager, without the microphone or speaker.

    .venv/bin/python drills/director_drill.py "Director, what needs me?" ["Are you the director?" ...]

Each utterance goes through Manager._dialogue_turn exactly as a transcribed
turn does, with the REAL `director ask` and `director --json status` against
Director's real store. Only the voice is replaced: every line the manager would
speak is printed instead. Prints a transcript.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))


async def main(utterances):
    from test_memory_manager import make_manager  # the same bare manager the tests build
    import manager
    m = make_manager()
    del m._director_inventory  # the real one: director --json status

    async def say(text, voice="manager", **_):
        who = "Director" if voice == "director" else "Tranquility"
        body = text[len("Director: "):] if text.startswith("Director: ") else text
        print(f"{who}: {body}")
        return True

    m._say = say
    with patch("manager.note"), patch("dialogue_manager.emit", AsyncMock()), patch("manager.emit", AsyncMock()):
        for u in utterances:
            print(f"you: {u}")
            await m._dialogue_turn(u, None, None)
        print("you: Who are the agents that you see?")
        await manager.Manager._fleet_inventory(m)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["Director, what needs me?"]))
