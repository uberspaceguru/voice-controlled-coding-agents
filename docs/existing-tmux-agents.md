# Existing terminal fleet

This increment separates observing a terminal from owning its lifecycle. A user
can keep existing Ghostty windows, tmux sockets, sessions, and worktrees while
Tranquility inventories them and proposes useful groupings.

## Read contract

`tbase fleet --json [--socket /absolute/path]` returns version 1 of a snapshot:
`snapshotId`, epoch-seconds `capturedAt`, `servers`, `panes`, and `warnings`.
Server status distinguishes `ok` (including a valid empty server), `unavailable`,
`error`, and `skipped`. Limits and incomplete identity reads remain visible.
The command runs before opening the queue database. It never calls ownership
reconciliation, which can otherwise adopt or rekey records during a read.

A pane ID hashes the canonical socket path and tmux pane ID. It identifies a
physical pane within this snapshot, not an immortal agent: server restarts can
reuse pane numbers. `attachedClientCount` describes the session's tmux clients,
not whether Ghostty is showing this pane. Linked windows count one physical pane.

Agent identity requires process ancestry and matching TTY, plus harness-specific
evidence. A process name or a window title alone cannot establish a conversation.
Ambiguous identities stay unresolved. Live process evidence does not prove the
agent is busy, ready for input, or safe to control. Manager speech counts verified
identities separately from shells, unresolved panes, activity reports, and voice
enrollment. Other known targets absent from this inventory are reported separately
because they might be inside an unresolved pane; counts must not be added.

## Proposal command

`tbase organize --output-dir /absolute/new-directory` captures a fresh snapshot
and runs `scripts/organize-tmux.py`. An installed CLI can use a helper colocated
with its binary or under its bundle's Resources directory. Development builds
also know their source checkout; `--helper /absolute/organize-tmux.py` explicitly
selects another copy. The caller's current directory is never searched for code.

`--socket` is repeatable; `--timeout-seconds` accepts 10 through 600 (default 180).
`--dry-run` writes the exact sanitized input, schema and invocation without a
model call. `--report-session UUID` attributes the generated report to its caller.
The standalone helper also accepts an explicitly requested model or CLI path.

The helper uses the existing CLI account, ignores user configuration, disables
tools, hooks, plugins, browsing and subagents, and requests a read-only sandbox.
It passes only whitelisted metadata on stdin. It neither gives the model a tool
to inspect terminals nor submits their scrollback. The new persisted analysis
thread is recorded in `result.json`; it is not a new tmux pane or TUI window.

The output schema contains groups, explanations, questions and unassigned pane
IDs. Code validates exact snapshot binding and complete, nonduplicated coverage.
No generated command, path, or action is executable. A failure, timeout, tool-use
event, incomplete turn, or invalid plan never becomes a successful proposal.
There is no automatic retry with fewer restrictions. Private artifacts include
the input, invocation, child events, response, receipt, and successful plan/page.
The report is static evidence; regenerate it when sessions change.

## Integration and rollback

Build `tbase` from the same revision as the Python manager before enabling the
new fleet read. A manager pointing at an older CLI reports inventory unavailable;
it does not silently fall back to an incomplete count. Neither compiling nor
running the two new CLI commands restarts the native app or changes its launcher.
Use the existing supervised activation flow when intentionally updating runtime.

Rollback restores the previous manager source and matching CLI path together.
No shared ownership schema, enrollment, terminal layout, provider credential,
or preference migration needs undoing. Organization artifacts can be retained as
historical proposals. Applying groups, safely adopting newly discovered agents,
and focusing their existing Ghostty panes are separate work; this increment
deliberately grants none of those capabilities through discovery.

## Acceptance

1. Record existing session/window layouts and leave the native app stopped.
2. Run `fleet --json`; compare all expected sockets and panes, including an
   explicit custom socket. Inspect verified versus unresolved identities.
3. Run `organize --dry-run` into a new directory; confirm no child thread starts.
4. Run one actual proposal. Verify the receipt says `proposed`, every observed
   pane appears exactly once, and the page explains uncertain groupings.
5. Compare layouts, session IDs, pane IDs and live processes with the baseline.
6. After separately activating a matching manager and CLI, ask how many agents
   are running, then ask for the list. Confirm shell panes and uncertain identities
   are not counted as verified agents. Voice delivery requires this live check;
   parser/handler tests alone do not prove audible playback.
