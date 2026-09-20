# Exact-value speech

Explicit directory, branch, command, and session-ID questions join the existing
intent choice. A small full-request matcher protects the directory regression
even when the classifier calls it an action. Existing addressedness rules still
apply: this does not turn the manager into an always-answering assistant.

After selection, this is a terminal read-only route. It cannot call the answer
model, dispatch a message, or execute a command. Directory and ID come from the
live target record for the agent on stage. Branch and command come only from one
explicit label in that session's last assistant message:

    Branch: `feature/example`
    Command: `git status --short`

The command requires inline code delimiters. Branch and command are announced as
"Last reported", not verified current state. Missing, conflicting, suspicious,
or truncated records produce an unknown-value answer. This first version does
not infer arbitrary identifiers or search old transcripts for unlabeled values.

An internal validated frame preserves the value through manager TTS. Ordinary
answers still pass through both existing spoken-text sanitizers. The exception
is scoped per asynchronous task/frame and reset even on failure. Exact values use
the manager voice because the app's agent-voice deep link sanitizes paths again.
No UI, app settings, provider credentials, or model tool schema changes.

## Checks

From `tb-voice/server`, with the project's dependencies available:

    python -B -m unittest discover -s tests -v
    python -B -m ruff check --no-cache manager.py spoken.py exact_values.py exact_speech.py tts.py tests/test_exact_values.py evals/run_exact_requests.py

The tests cover read-only routing despite an erroneous action classification,
missing/stale targets, known values, truncation, ordinary summaries, explicit
actions, an unaddressed request, and concurrent TTS sanitizer isolation.

Optional live classification evaluation (synthetic corpus only):

    JEV_API_KEY=<provided securely by environment> python -B evals/run_exact_requests.py

This calls only the classifier, suppresses its disk call log, and never loads
real conversations or starts the app. Do not put a real key in shell history.
`exact_requests.observed.json` records a local observation, not a deterministic
guarantee. An initial run classified a mixed read/delete request as read-only;
explicit mixed-request criteria fixed that case on the second run (13/13).
Action cases accept either direct dispatch intent or the existing custom route.
This score measures intent selection only, not addressedness or live audio.

## Limits

- A session must be on stage; no automatic target selection for exact answers.
- The last-assistant-message API caps text at 600 characters. Messages reaching
  that boundary fail closed, including complete values inside a longer message.
- Reported branch/command text may be stale or incorrect at its source.
- Unknown phrasings still depend on probabilistic intent classification.
- Literal text reaches the synthesizer unchanged; pronunciation of punctuation,
  long-path playback, microphone delivery, and interruption require a live test.
  The TTS test stubs the provider, using the installed pipeline's awaited-call
  contract; it does not prove audio output.
- Basic credential-shaped value rejection is conservative, not a secret scanner.
