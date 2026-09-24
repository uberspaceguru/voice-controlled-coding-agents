#!/usr/bin/env python3
"""The agent vocative, without the pipeline: `python3 drills/vocative_drill.py`.

Every case is a sentence the transcriber can actually produce. Exits non-zero
on the first that misroutes, and prints the table either way.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vocative import fold, names_an_agent  # noqa: E402

NAMES = ["Director", "Yobi1", "Sys-3PO", "Director Two", "Notes"]

CASES = [
    # (heard, expected name, expected remainder)
    ("Director, ship the fix.", "Director", "ship the fix."),
    ("Director. What's blocking the design work?", "Director", "What's blocking the design work?"),
    ("director, run the tests", "Director", "run the tests"),
    ("Director", "Director", ""),
    ("Director.", "Director", ""),
    ("Yobi one, what are you doing?", "Yobi1", "what are you doing?"),
    ("Yobi 1: status", "Yobi1", "status"),
    ("Sys three P O, restart the printer.", "Sys-3PO", "restart the printer."),
    ("Sys-3PO, hello", "Sys-3PO", "hello"),
    ("Director two, take over.", "Director Two", "take over."),
    ("Notes, remember to call the bank.", "Notes", "remember to call the bank."),
    # Not a vocative: the name is not the opening, or is not followed by a pause.
    ("the director said no", None, None),
    ("director of engineering wants a demo", None, None),
    ("Tranquility, tell Director to ship it.", None, None),
    ("I think Yobi one is done.", None, None),
    ("", None, None),
]


def main() -> int:
    failed = 0
    assert fold("Sys-3PO") == "systhreepo", fold("Sys-3PO")
    assert fold("Yobi one") == "yobione", fold("Yobi one")
    for heard, name, rest in CASES:
        got = names_an_agent(heard, NAMES)
        want = (name, rest) if name else None
        ok = got == want
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {heard!r:48} -> {got!r}" + ("" if ok else f"  (wanted {want!r})"))
    print(f"{len(CASES) - failed}/{len(CASES)} routed as expected")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
