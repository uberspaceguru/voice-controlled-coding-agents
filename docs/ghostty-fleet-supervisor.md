# Existing terminals and a continuing supervisor

This branch adds three connected paths: Ghostty presentation, verified local
tmux discovery/control, and an optional persistent Codex manager conversation.
It also includes the explicit Quit control from the separate shutdown branch.
Nothing here requires a separate worktree per terminal. Use a worktree when
agents need independent Git edits; tmux sessions and windows remain your own.

## What the manager can do

- Discover local sockets from the environment, standard locations, tmux process
  sockets, and optional `tmux.socketPaths` in `~/.claude/hq.json`.
- Inventory every discovered pane. Dispatch only to a unique verified agent
  identity. Shells and ambiguous/unidentified panes remain visible observations.
- Send an explicitly directed message through the original socket and pane.
  Discovery does not enroll, resume, move, rename, terminate, or take ownership.
  Unsent drafts and copy mode cause refusal without clearing them.
- Open a verified agent in the selected terminal with `tbase focus SESSION_ID`.
  The voice route is distinct from sending that phrase as agent work.
- With `manager.backend=codex`, answer fleet questions in one durable Codex
  thread. `codex exec resume` uses its saved thread ID across process restarts.
  It has no shell/browser/MCP tools and cannot dispatch its generated prose.
  Jev and the existing dialogue policy own direct sends, exact facts, and controls.
- Observe source-backed identity and stored briefs every 20 seconds without a
  model call. A question makes one supervisor call. Persist actual send receipts,
  output completion versus interruption/unknown, and labeled inferred work notes.
  A running process is not proof of work progress; output completion is not proof
  the user heard or acknowledged it. Old confirmations are never restored.

## Ghostty behavior and limits

Ghostty 1.3+ scripting is required. `automatic` chooses a running scriptable
Ghostty, otherwise Terminal; explicit `ghostty` fails visibly rather than silently
switching applications. macOS may require Automation permission for the actual
Dev bundle controlling Ghostty. No permission reset is part of this change.

Ghostty exposes stable surface IDs but no TTY/PID mapping. A known verified
surface can be raised. An arbitrary existing tab cannot safely be guessed from
its title or directory, so focus opens an additional **view of existing tmux
windows**, not another coding agent. It adds a marked `tb-view-*` grouped session
on the same server. Original window selections, layouts and processes are checked.
It never uses `attach -d` or a resize command. Group membership does change.

Multi-pane windows require a new view attachment because current tmux does not
expose the client's `active-pane` selection for reliable cache validation.
Zoom-hidden panes and cases where unchanged window sizing cannot be proven are
refused. Closing a view tab detaches it; a marked detached view may be reused.
No existing session is killed as cleanup. Removing the opt-in configuration
does not delete views or conversations.

## Activation — perform after runtime review

The development worktree, compiled test executable and running app are separate.
These settings do not activate code in an old native bundle by themselves.

1. Record the current native bundle, manager launcher, launcher source directory,
   `hq.json`, and `terminal-host.json`. Preserve the existing provider environment,
   Python environment, API credentials and agent sessions.
2. Build/package this committed branch through the repository Dev lane into a
   separate prepared artifact. Do not overwrite the installed production app.
3. Use **this branch's** `tbase` with the intended support directory:

   ```sh
   /absolute/path/to/new/tbase terminal ghostty
   /absolute/path/to/new/tbase manager-backend codex
   /absolute/path/to/new/tbase fleet --json
   /absolute/path/to/new/tbase targets --json
   ```

   The latter two are read-only. Verify inventory before attempting dispatch.
   Add unusual sockets only as absolute `tmux.socketPaths` entries, preserving
   other `hq.json` fields. No tmux server is created by discovery.
4. Point the existing machine-local manager launcher at this branch's
   `tb-voice/server` and the matching new `tbase`. Reuse its known working Python
   environment and Gradium credentials. Keep its direct child process ownership.
   The native app passes `TB_MANAGER_BACKEND`; an independently hosted manager
   can use `TB_MANAGER_BACKEND=codex`. No API provider swap is required.
5. Start the exact prepared Dev bundle once, confirm its process path and manager
   readiness, and do the spoken acceptance below. A changed ad-hoc signature may
   require the user to refresh permission for that exact bundle. A passing build
   does not establish microphone, playback or permission readiness.

The private supervisor state defaults to
`~/Library/Application Support/VoiceDispatch/supervisor`, or
`$VOICE_DISPATCH_SUPPORT_DIR/supervisor`. `TB_SUPERVISOR_STATE_DIR` overrides it
for an isolated drill. Codex uses the user's existing CLI account login. The
manager's process exits between turns; its actual conversation thread persists.

## Rollback

Stop the new manager/app. Restore the recorded launcher, native bundle and
configuration files, or select `tbase manager-backend dialogue` and the prior
terminal preference. Preserve the new supervisor directory for diagnosis; it
does not control any worker process. Relaunch the previous exact bundle only
when requested. No agent/session migration or Git rollback is necessary.

## Short live acceptance

1. With two existing agents on different tmux servers, ask “How many agents do
   I have running?” Compare against `fleet` and `targets`; unknown work state
   must remain unknown.
2. Ask “What is everyone working on?” then “Which part still needs my attention?”
   Verify the second turn continues the same manager thread and cites actual
   stored notes. Agents without notes must be described as unknown.
3. “Open Alpha's terminal.” It should focus/open the existing pane in Ghostty,
   without sending that sentence to Alpha or moving another terminal's view.
4. “Tell Beta to report its current branch, without changing files.” Check the
   correct server receives it. “What exact command did you send?” must be a
   literal receipt, with no second dispatch.
5. Leave a typed draft in Beta or enter copy mode. A directed send must refuse
   while preserving that state. Remove the draft manually before retrying.
6. “Stop speaking.” Then quit through the visible Quit control. Audio and the
   manager child should stop while the existing tmux agents remain alive.

## Automated checks

Python provider tests mock devices and providers. The separate synthetic live
provider smoke proves conversation resume, not audio. Jev evaluation records
classification and local routing latency, never time to first audio.

The opt-in `TmuxTerminalViewIntegrationTests` creates its own exact temporary
socket and PTY clients. `ExactTmuxTransportIntegrationTests` creates disposable
servers with colliding pane IDs and exercises real input/readback, drafts, copy
mode and concurrency. The latter requires a fresh `/private/tmp` support
directory and `TB_RUN_EXACT_TMUX_TESTS=1`; both clean only their own sockets.
The isolated native `--selftest-quit-control` drill runs before AppDelegate and
checks external fleet row identities and Quit controls without starting audio.
