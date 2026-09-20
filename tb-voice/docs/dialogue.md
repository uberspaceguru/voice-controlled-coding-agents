# Stateful speech-act routing

The manager now asks seven independent semantic questions in one request:
addressedness, speech act, existing route, concrete target, recorded source,
response form, and present execution intent. `dialogue_questions.py` supplies
bounded candidates. `dialogue.py` owns permission, freshness, transitions, and
exactly-once dispatch. A probability is evidence about interpretation, not consent.
The existing text-generation providers remain unchanged and cannot invoke tools
from an informational answer.

## Executable policies

- A fresh, unambiguous direct instruction goes to the selected live agent without
  a blanket confirmation. The original text is copied; no generated command is
  executed. Source/target require .80 probability, act .88, addressedness .70,
  and present execution intent .80. These are local operating thresholds, not a
  safety guarantee. Referenced commands/proposals are read back and require fresh
  confirmation; launching another agent also requires confirmation.
- Bare okay/mm-hmm and thinking aloud are silent. A yes cannot authorize a missing,
  unoffered, held, expired, or stage-mismatched proposal. Pending proposals expire
  after 45 seconds; referenced commands/actions after 90 seconds. Duplicate final
  transcripts cannot dispatch twice. A new actual user turn can repeat an action.
- Corrections bind unsent payloads or current information requests. An already
  dispatched request is never silently retargeted/replayed or described as undone.
  Exact facts come only from current metadata or explicitly labeled stored facts;
  ordinary summaries keep the existing sanitizer. Detail has a 120-word budget.
- Stop speech uses existing interruption/mute paths. Pause requests keeps the
  microphone/STT running for resume controls. Hold/resume concerns unsent work;
  resume presents it again. Asking an agent to stop sends a stop request, with a
  receipt that does not claim the task actually stopped.
- Repeated unresolved clarification of the same issue ends with a receipt rather
  than an indefinite confirmation loop. A change of essential details can warrant
  a new focused clarification.

## Interruption and delivery evidence

Hearing pauses the execution boundary. A classified backchannel/side conversation
releases it without discarding the ongoing answer. A meaningful new request
advances the epoch and supersedes old uncommitted work. Failed classification
invalidates paused work rather than silently resuming an old send. A confirmed
send already crossing the subprocess boundary is observed, never auto-retried.

`DialogueSpeakFrame` carries an epoch/stage guard through asynchronous synthesis.
`speech_delivery.py` correlates TTS contexts and counts generated versus forwarded
output audio. Completion requires synthesis completion, matching start/stop, and
successful output for the full generated duration, plus natural provider end-of-stream.
A queue timeout after partial synthesis is unknown, not complete. Generic bot-stop events, late
stops, provider failure, timeouts, interrupted or ambiguous output cannot offer a
proposal. One interrupted answer may restart after a noninvalidating backchannel;
there is no unbounded playback retry.

This is **transport-observable completion, not proof of hearing**. The installed
transport strips context IDs while rechunking audio; the observer correlates
ordered start/stop markers per destination and fails closed on overlap. Native
app deep-link speech has no correlated completion receipt and is only logged as
`queued_native`. Existing old `spoken` logs are not delivery/acknowledgment evidence.
An empty finalized user turn invalidates paused work and releases the boundary;
it cannot be treated as a confirmed backchannel. A held fragment or an active
classifier retains ownership of its own input settlement.

Events expose judgments/distributions, selected operation, reason, target,
pending ID, routing milliseconds, action delivery result, and speech delivery
status. Exit status determines sent/waiting/failed/unknown receipts. Successful
send is not completed coding work.

## Validation and limits

Run from `tb-voice/server` using the existing environment:

```sh
python -B -m unittest discover -s tests -q
python -B -m ruff check --select I,UP,F dialogue*.py speech_delivery.py exact_speech.py tts.py bot.py tests evals/run_dialogue.py evals/dialogue_fixture.py
```

The milestone has 103 passing tests. Tests mock providers and subprocesses but exercise real manager handlers, frame
entry, cancellation races, installed provider handoff and transport output loop.
They do not establish audible end-to-end behavior.

The initial 64-turn corpus has 44 development and 20 initially held-out turns,
run twice: development **86/88**, held-out **26/40** policy matches. That held-out
set was subsequently inspected and is diagnostic, not unseen anymore. Its eight
unnecessary clarifications were 8/128 overall, or 8/96 cases not expecting one;
it missed 2/14 expected sends. No unexpected/wrong-target/wrong-payload dispatch
was observed. Contract coverage itself missed two wrong-focus clarifications.

A new frozen 20-turn set run twice scored **27/40**: four unnecessary
clarifications (4/40 overall; 4/30 eligible), three silent repairs, six response
mode errors; two repairs failed to clear pending work. No unexpected dispatch
was observed, and all four expected sends occurred. It is a small synthetic
sample, not evidence of production reliability. Subsequent structural fixes stop
an independent summary-route vote from replacing an exact answer and stop sent
records being labeled unsent. These fixes were made after diagnosing that set;
its 27/40 result remains reported unchanged.

HTTP latency: initial combined evaluation n=126, median189.47 ms/p95328.20 ms;
fresh set n=40, median184.76 ms/p95450.96 ms. Synthetic routing for the latter was
median184.92 ms/p95451.12 ms. Neither measures STT, text generation, or time to
first audio. Through the final challenge, 319 live requests (seven judgments each) were made
across bounded development/evaluation runs, using synthetic text only. Monetary
cost was not metered. Raw evidence is in the session report archive; the runner
supports `--corpus`, `--split`, `--runs`, and `--output`, and records source hashes.
Do not load personal transcripts into the live evaluation.


A final newly authored post-diagnosis challenge (12 turns, two runs) scored
**18/24** policy matches. It is not an independent benchmark. HTTP n=24,
median162.86 ms/p95281.07 ms; synthetic routing median163.16 ms/p95281.24 ms.
No unexpected dispatch or HTTP/local error was observed. Four of six expected
sends were missed (three silent outcomes, one unnecessary confirmation). Two
negated branch readbacks also stayed silent. The classifier still
makes material mistakes; live acceptance and broader independent evaluation are
required before describing this as reliable conversation handling.

## Integration and rollback

This branch builds on exact-value commit `d19c3c6` and the integrated manager
snapshot at `9ed33a9`. It has not been activated in the user's working app.

1. In a separate integration worktree based on the intended current manager
   branch, cherry-pick the exact-value change if absent, then this milestone and
   any following memory commit. Run the Python checks above. Run the repository
   source/preflight checks before landing; no native rebuild is needed just to
   review these Python changes.
2. Later manager changes alter provider and interruption ownership. When resolving
   conflicts, retain their active STT/TTS providers, echo gate, voice lock, and
   turn-end strategy. Port the guarded speech/delivery observer to that provider;
   do not overwrite it wholesale with this older provider file. Re-run provider
   and transport tests against the actual integration version.
3. Yobi1 owns activation: retain the old launcher command/source revision, then
   point only the manager child at the integrated server. Restart that child when
   Ahmed is ready. Do not rebuild/relaunch the native app or reset TCC for this.
4. Rollback: restore the recorded previous manager launcher/source and restart
   only that child. Revert feature commits on an integration branch if needed;
   do not reset a shared worktree or delete credentials.

Live acceptance requires checking literal path pronunciation, natural turn timing,
backchannel interruption/resumption, actual sent receipts, and trace correlation
on one real spoken exchange. A passing synthetic TTS test is not that check.
