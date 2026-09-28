# W60 — branch delivery

Repo: https://github.com/uberspaceguru/voice-controlled-coding-agents
Branch: ahmed/ghostty-fleet-manager
Remote: ahmed
Tested code: 3eb2b0bf3f3c715dd1bfd64a407fa9271f9fb275
Date: 2026-09-28

Scope: Ghostty support, verified control of existing local tmux agents, and a
persistent Codex supervisor conversation. The Director handoff calls W60
“Jev's OpenRouter integration”; that label does not describe the completed
work. OpenRouter was deferred and is not implemented or claimed here.

Tests:

- `scripts/test.sh`: 2,235 XCTest cases, 37 skipped, zero failures; all 76
  Swift Testing cases passed. Both compiled test inventories verified.
- Python `unittest discover -s tests -p 'test_*.py'`: 284 passed.
- Explicit disposable tmux integration: four passed, none skipped. Covers
  exact server/pane routing, drafts, copy mode, stale identity, concurrent
  buffers, and preservation of original selection/layout/zoom.
- Source checks for key names, house copy, borrowed descriptors,
  compatibility comments, and row timestamps passed.
- `git diff --check` passed.

Fixes in this delivery:

- PR page tests now create disposable repositories with upstream and fork
  origins. Assertions remain intact and no longer depend on this checkout.
- The first full run stalled while a cleanup test enumerated the large shared
  temporary directory. A process sample identified the cause. The test now
  uses a private parent, proves it is empty afterward, and proves a sibling
  file survives. The drill's default runtime location is unchanged. The
  complete rerun passed; the initial diagnostic is retained locally.

What remains:

- Review/merge and deliberate runtime activation. No app or manager was
  relaunched, and no shared credentials, provider settings, or working
  terminal sessions were changed for activation.
- Live voice and Ghostty Automation acceptance, plus a fresh live evaluation
  of the earlier readback repair. Unit/integration success is not proof of
  audible end-to-end behavior.
- Full native UI/packaging/lifecycle preflight was not rerun under the explicit
  no-relaunch instruction. This delivery publishes a feature branch, not a
  main merge or release.
- OpenRouter provider integration remains deferred.

Integration and rollback: `docs/ghostty-fleet-supervisor.md`.
