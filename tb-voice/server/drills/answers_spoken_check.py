#!/usr/bin/env python3
"""Every answer the manager hands the app is SPOKEN (25 Sep, tb-speak).

    python3 drills/answers_spoken_check.py [app.log] [--since 2026-09-25T22:10]

The manager's `answer` event carries a right-hand's reply and synthesizes
nothing; the app must play it, hands-free on or off. Silent hands-free was
exactly this: the app took the event, updated the orb, and played nothing.
This reads the host app's log and checks that each "manager: answer from …"
line is followed, within WINDOW seconds and before the next answer, by audio:
an ElevenLabs synth, or a cached clip (which plays without one). Exit 1 on the
first answer nobody spoke.
"""
import os
import re
import sys
from datetime import datetime

WINDOW = 15.0
LOG = os.path.expanduser("~/Library/Application Support/VoiceDispatch-Director/app.log")
STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z")
ANSWER = re.compile(r"manager: answer from (.+?) for ")
AUDIO = re.compile(r"11labs: synth |11labs: chain: clip HIT")


def when(line):
    m = STAMP.match(line)
    return datetime.fromisoformat(m.group(1)) if m else None


def main(argv):
    path = next((a for a in argv[1:] if not a.startswith("--") and not re.match(r"\d{4}-", a)), LOG)
    since = argv[argv.index("--since") + 1] if "--since" in argv else ""
    lines = [l for l in open(path, errors="replace") if l[:len(since)] >= since]
    answers = [(i, when(l), ANSWER.search(l).group(1)) for i, l in enumerate(lines) if ANSWER.search(l)]
    if not answers:
        print("no answer events in the log" + (f" since {since}" if since else ""))
        return 1
    bad = 0
    for k, (i, t, name) in enumerate(answers):
        end = answers[k + 1][0] if k + 1 < len(answers) else len(lines)
        heard = next((l for l in lines[i + 1:end]
                      if AUDIO.search(l) and when(l) and (when(l) - t).total_seconds() <= WINDOW), None)
        if heard:
            print(f"SPOKEN  {t:%H:%M:%S} {name}: {heard.strip()[:120]}")
        else:
            bad += 1
            print(f"SILENT  {t:%H:%M:%S} {name}: no synth within {WINDOW:.0f}s")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
