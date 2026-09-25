#!/usr/bin/env python3
"""Drive hands-free with the real microphone: the manager exactly as the
Director app starts it (hq.json's launcher, the app's environment), each line
spoken aloud with `say`, each answer spoken aloud where the card would speak
it, then ten seconds of silence. Prints a transcript from the event stream.

Speaks aloud on this Mac, and the asks are real: "Tell the Whisper worker yes"
answers whatever that worker has open. Run it only when that is meant.

    /usr/bin/python3 drills/handsfree_mic_drill.py ["line" ...]
"""
import json, os, subprocess, sys, threading, time

HOME = os.path.expanduser("~")
OUT = os.getenv("DRILL_OUT", "/tmp")
SUPPORT = f"{HOME}/Library/Application Support/VoiceDispatch-Director"
LINES = ["Director, what needs me?", "Tell me more about the first one.",
         "Tell the Whisper worker yes.", "Yobi one, what's my day?"]
if len(sys.argv) > 1:
    LINES = sys.argv[1:]
env = dict(os.environ)
env.update(TB_HOST="app", TB_URL_SCHEME="tbdirector", VOICE_DISPATCH_SUPPORT_DIR=SUPPORT,
           TB_RIGHT_HAND_CARDS="1", TB_DEFAULT_INTERLOCUTOR="director")
env["PATH"] = ":".join([f"{HOME}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", env.get("PATH", "")])
argv = json.load(open(f"{HOME}/.claude/hq.json"))["manager"]["command"]
proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=open(os.path.join(OUT, "bot.stderr"), "w"), text=True)
events, lock = [], threading.Lock()
log = open(os.path.join(OUT, "events.jsonl"), "w")

def read():
    for raw in proc.stdout:
        log.write(raw); log.flush()
        try: e = json.loads(raw)
        except ValueError: continue
        with lock: events.append((time.monotonic(), e))
threading.Thread(target=read, daemon=True).start()

def wait_for(pred, since, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with lock:
            for t, e in events:
                if t >= since and pred(e): return e
        time.sleep(0.2)
    return None

try:
    if not wait_for(lambda e: e.get("event") == "ready", 0, 60):
        print("manager never became ready"); sys.exit(1)
    time.sleep(1.5)
    for line in LINES:
        print(f"you (said aloud): {line}")
        t0 = time.monotonic()
        subprocess.run(["say", "-v", "Samantha", line])
        heard = wait_for(lambda e: e.get("event") in ("addressed", "listening") and e.get("text"), t0, 20)
        print(f"  heard as: {heard.get('text') if heard else '(nothing transcribed)'}")
        ans = wait_for(lambda e: e.get("event") == "answer" or (e.get("event") == "speaking" and e.get("text")), t0, 45)
        if not ans:
            print("  (no answer)"); continue
        who = ans.get("name") or "Tranquility"
        print(f"  {who} (on {who}'s card): {ans.get('text')}")
        print(f"    ({time.monotonic() - t0:.1f}s from the end of speaking)")
        t_ans = time.monotonic()
        subprocess.run(["say", "-v", "Daniel", ans.get("text", "")])   # the card's voice, as echo
        # The manager mutes its mic for the card's estimated length (manager._card_secs)
        # plus the echo tail; a person speaks after the card, so this does too.
        est = min(30.0, 1.5 + 0.42 * len(ans.get("text", "").split()))
        time.sleep(max(0.0, t_ans + est + 1.0 - time.monotonic()))
    print("you: (silence, 10 s)")
    t0 = time.monotonic()
    time.sleep(10)
    with lock:
        after = [e for t, e in events if t >= t0 and e.get("event") in ("addressed", "answer", "speaking", "tool")]
    print("  (nothing said)" if not after else f"  AFTER SILENCE: {after}")
finally:
    proc.terminate()
    try: proc.wait(5)
    except subprocess.TimeoutExpired: proc.kill()
