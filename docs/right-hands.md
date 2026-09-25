# Right-hands: the panel for a few agents, and a director's card of projects

Built 23 Sep 2026, from the user's own words:

> "The big thing I wish we had was YOU, the Director, more solid, more with
> Tranquility… Instead of a listing of every agent, just the agents I'd need to
> know about, or maybe the higher-level projects. I don't want to be overwhelmed
> with a long-ass list, but if I summon you, we can dive deeper."

## The file

`~/Library/Application Support/VoiceDispatch/right-hands.json`. **Absent, nothing
changes.** Present, only the sessions it names are on the grid, chime, count in
the menu bar, and come up on ⌃⌥. Every other session still flows through the
hooks into `queue.sqlite` — a director that reads the store sees everyone — and
sits on the Past Agents list, one page away.

```json
{
  "hands": [
    {"name": "Director", "session": "87469f47-f2b2-410e-9ac5-58c6363a19f3",
     "cwd": "/Users/ahmed/Code/myAgents/Director", "tmux": "DirectorCC",
     "rollup": "~/.director/rollup.json"},
    {"name": "Yobi1",   "session": "e781aff1-defa-4367-a184-437d093ca87e", "tmux": "y1-cc"},
    {"name": "Sys-3PO", "session": "2b973845-36c8-4c7d-8d31-18b6bc13821c", "tmux": "S3PO-macbook"}
  ],
  "summarizeOthers": false
}
```

A hand matches by any one of three keys:

- `session`: the id, or a prefix of eight or more characters.
- `cwd`: the working directory, **exact** and never a prefix. Measured 23 Sep:
  the director's sub-sessions run in the director's OWN directory, so a `cwd`
  key on the director made three "Director" rows out of one. Key a director by
  `session` and `tmux`; keep `cwd` for an agent that has a directory to itself.
- `tmux`: the tmux session name in `session-ownership.json`, which survives a
  restart when the id does not.

`name` is pinned: it replaces the harness title on the grid, the card, in
`tbase targets --json`, and in the manager's key terms, so the transcriber is
taught to hear it. `rollup` is described below. `summarizeOthers` keeps
preparing a brief per Stop for the sessions that are not hands (a model call
each), for a director that reads `tbase brief` for everyone; off by default.

A bare array also parses, for a file written in a hurry: a uuid is a session, a
path is a directory. A file that is present but malformed fails **open** to
everyone and says so in `app.log` (`right-hands: … unreadable`), because an
empty grid from a typo is a fleet hidden by a comma.

The lamp switch is not this and cannot be: its whole policy is that a waiting
turn un-files a session (`LampSwitch.isOff`). A hand-less row is filed without
that exception, and nothing is written for it (`GridRows.swift`, before the
switch). Do not reach for hook wrappers or `VOICE_LOOP_MARKER` instead: they
starve the store, and the store is what the director reads.

Where the cut is made:

| Surface | Where |
|---|---|
| Grid, chime, count, glow | `GridAssembler.rows` files non-hands; `EarconGate.arrivalKeys` sees no green lamp on a filed row |
| ⌃⌥, replay, badge, prefetch | `Coordinator.attended()`; `waiting()` stays whole |
| `tbase targets/status --json` | every row carries `rightHand` when a roster exists; the manager keeps the hands (`_right_hands_only`) |
| Names | `GridAssembler.harnessTitle` asks `RightHands.pinnedName` first |

## The rollup: a director's card shows projects, not agents

A hand with a `rollup` path keeps a file of the projects it is running. That
file is the card: read when the card is opened, never prepared ahead of time,
never served from a stored copy, so the projects are as they stand.

```json
{"updatedAt": "2026-09-23T19:40:00Z",
 "projects": [
   {"name": "Yobi1 design", "state": "needs you", "line": "Your call on the nav."},
   {"name": "Firebase",     "state": "ready",     "line": "Migration is green."},
   {"name": "GA fab",       "state": "moving",    "line": "Writing the eval harness."}]}
```

At most five projects (the sixth is the list the user asked not to see), sorted
needs-you, ready, moving. States are read leniently (`needs_you`, `blocked`,
`waiting` all mean the first; anything unknown is `moving`). The spoken line is
"Director. 3 projects: 1 needs you, 1 ready, 1 moving. Yobi1 design needs you.
Your call on the nav. …"; the card's question is the first needs-you line. An
unreadable rollup falls back to the ordinary summary of the last turn, with a
`routing:` log line, never to silence. `tbase brief <id> --json` reads the same
file, so the manager's rungs and the hub agree with the card.

## Summoning a hand by name

The hands-free manager (`tb-voice`) treats a turn that OPENS with a hand's name
as a message for that hand — "Director, ship the fix." — with no model asked
whether the manager was addressed (`vocative.py`, `Manager._address_agent`).
The hand takes the stage; the words after the name are composed into the message
and typed in through `tbase send`. The name alone ("Director.") opens dictation
("For Director. Go ahead."). "Tranquility, tell Director to …" still works as it
did. Names are folded before comparison, so "Yobi one" is `Yobi1` and "Sys three
P O" is `Sys-3PO`. `drills/vocative_drill.py` runs the cases without the
pipeline.

Opening a hand's card by hand, when that hand has a rollup, puts it on the
manager's stage: the app writes `{"cmd":"stage","session":…,"name":…}` down the
child's stdin (local) or as a text frame (hosted), and the manager says "Director
is on stage. Ask about any project, or tell it what to do." If hands-free is off
and configured, the card starts it and hands the stage over on `ready`. ⌃⌥ never
does this; only a pick does, because the automatic path must not turn a
microphone on.

## The preview lane, and what was wrong with it

- `switch-app.sh` refused both lanes because every bundle was stamped
  `TBDatabaseSchemaVersion` 18 while the database was at v22. Upstream fixed the
  stamp in #623 (bundle.sh reads the highest migration). This branch adds the
  other half: `switch-app.sh` reads a bundle with no stamp by its source
  commit's migrations (`scripts/lib/schema-version.sh`) before falling back to
  18. `scripts/test-schema-version.sh` checks both agree.
- A manager child started by Prod with `TB_URL_SCHEME=tbdev` opened links Prod
  does not claim. `ManagerConfig.environment` now hands the child this lane's
  own scheme (`AppIdentity.urlScheme`: `tbdev` for Dev, `tranquilitybase` for
  Prod). A launcher script that sets the variable itself inside the child still
  overrides it; `~/.yobi1/tranquility-dev/run-manager.py` did on 23 Sep.

## A hand with a brain (24 Sep)

Director stopped being a Claude session: it is the `director` command and its
tick. A hand can therefore carry two commands instead of a pane:

```json
{"name": "Director", "session": "87469f47-f2b2-410e-9ac5-58c6363a19f3",
 "projects": ["director", "--json", "status"],
 "ask": ["director", "ask", "{text}", "--channel", "tranquility", "--external-id", "{conversation}"]}
```

- `projects` is the card, run each time it opens. Its output may be the rollup
  shape or Director's own status JSON: needs-you agents first, then working,
  five at most, with the whole fleet counted in the first line.
- `ask` is the brain. A reply to the card, spoken or typed, goes through
  `BrainTransport` (the remote agents' door) instead of being typed into a
  pane, and the answer is spoken on the card in the hand's voice. `{text}` is
  one argv element; nothing reaches a shell. `{conversation}` is the hand's
  session id, so card and voice are one thread in Director's `conversation`.
- In hands-free, "Director, …" and anything said with Director on stage go to
  `tbase ask`, and the answer is spoken through the app's `say` verb.
- A hand with a brain stands green on the grid whether or not a process runs
  under its id, never offers revive, and is listed by `tbase targets --json`
  with `"asks": true`.

## Why Director's hails timed out (-1712)

`open -g tranquilitybase://say?...` returns -1712 (errAETimeout) when the app
that owns the scheme does not answer the URL Apple Event in time. Director's
`notify_log` shows hails working at 19:47 and 19:48 on 23 Sep and failing with
-1712 on every attempt from 05:48 on 24 Sep. Prod 1307's own log stops
recording any main-thread activity (menu bar checks, hotkeys, HUD) after 22:30
on 23 Sep, while a background loop kept logging "secrets: read" four times
every 1.5 s, which is also what wrote 2 GB of log a day (the 23 Sep disk-writes
diagnostic report). The app's main thread was stuck, so it could not take the
URL event. No spindump was captured, so the blocking call is not identified.

A preview must also own the scheme while it runs: LaunchServices sends a link
to the scheme's default app and launches it if needed, and the default for
`tranquilitybase://` is Prod. With Prod stopped, a hail to the old scheme
starts Prod.

## Talking to a right-hand by name (25 Sep)

In Tranquility Base Director each right-hand is addressed by name, not through
Tranquility: "Director, …", "Yobi1, …", "Sys-3PO, …". The voice manager
(`director_link.route`) recognises the name as the transcriber writes it and,
because this app sets `TB_RIGHT_HAND_CARDS=1`, hands the turn over as a
`ManagerEvent.ask` (the hand's session, name and words). The app asks the
hand's brain (`askBrain`) and speaks the answer on that hand's card.

| Hand | Brain (`ask`) |
|---|---|
| Director | `director ask "{text}" --channel tranquility --external-id {conversation}` |
| Yobi1 | `brains/yobi1-ask`: Yobi1 Fable's `--compose "<text>"` (it has no URL scheme or ask command), card line cut to two sentences |
| Sys-3PO | `brains/sys3po-ask`: health and "what needs me" from `s3po-health status`; anything else typed into its pane with `director send`, the reply read back from its transcript |
| TeamChat Manager | none: a greyed placeholder that says it isn't connected yet |

`scripts/install-director.sh` copies the brains into the app's own folder on
every install and gives Yobi1 and Sys-3PO their `ask` in that folder's roster
when they have none. The `ask` runs them through `/usr/bin/python3`: the app keeps
every file in its folder at 600, so nothing there is executable. Prod's roster is never touched.
