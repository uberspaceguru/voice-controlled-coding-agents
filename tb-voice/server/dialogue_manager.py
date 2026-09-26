"""Connect the explicit dialogue policy to the manager's existing read/speak doors."""

import asyncio
import os
import time

from loguru import logger
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass

from dialogue import Dialogue, chosen
from dialogue_questions import build_questions, judgment_state
from events import emit
from exact_values import ExactValue
from memory_manager import MemoryManagerMixin
from turn_end import HOLD_SECS, holds_floor

# A finished reply waits for his words, not for sound: with no new words for
# this long it is spoken, whatever VAD hears (25 Sep 18:19: 27.6 s held behind
# room sound; research/turn-taking.md R4/R6).
FLOOR_WAIT_SECS = float(os.getenv("TB_FLOOR_WAIT_SECS", "1.5"))


@dataclass
class TurnGuard:
    epoch: int
    stage: str | None


@dataclass
class OpenTurn:
    """The last committed turn, while its reply has not started: late speech
    joins it (research/turn-taking.md R3). `route` is set once it is known to
    be an ask of Director or a hand; `replying` once its reply is spoken."""
    text: str
    task: asyncio.Task
    route: tuple | None = None
    replying: bool = False


CURRENT_TURN = ContextVar("manager_dialogue_turn", default=None)
# Set while a delay token ("The GPU one. One sec.") is spoken: that is not the
# reply, and speech after it still joins the turn.
BRIDGING = ContextVar("manager_bridging", default=False)


class DialogueManagerMixin(MemoryManagerMixin):
    def _init_dialogue(self):
        from speech_delivery import DeliveryBook
        self.dialogue = Dialogue()
        self._init_memory()
        self.deliverybook = DeliveryBook()
        self._last_delivery = None
        self._handler = None
        self._active_work = None
        self._judging = None
        self._input_serial = 0
        self._input_ready = asyncio.Event()
        self._input_ready.set()
        self._deliveries = set()
        self._open_turn = None
        self._last_words = 0.0
        self._last_words_text = ""
        self._floor_taken = None

    def _current(self):
        guard = CURRENT_TURN.get()
        return guard is None or (
            self.dialogue.epoch == guard.epoch
            and (self.stage or {}).get("sessionId") == guard.stage
        )

    def _require_current(self):
        if not self._current():
            raise asyncio.CancelledError("superseded dialogue turn")

    def _speech_guard(self):
        guard = CURRENT_TURN.get()
        if guard is None:
            return None
        epoch, stage = guard.epoch, guard.stage
        return lambda: (self.dialogue.epoch == epoch
                        and (self.stage or {}).get("sessionId") == stage)

    def _stage_changed(self):
        self.dialogue.sync((self.stage or {}).get("sessionId"), list(self.dialogue.targets.values()))
        guard = CURRENT_TURN.get()
        if guard:
            guard.stage = self.dialogue.stage

    def barged_in(self):
        """He took the turn over the voice (barge_in.py said stop or claim)."""
        self._barge_in_at = time.monotonic()

    def _took_floor(self, within: float = 15.0) -> bool:
        """Once per barge-in: was this turn started over the voice?"""
        at = getattr(self, "_barge_in_at", None)
        self._barge_in_at = None
        return at is not None and time.monotonic() - at < within

    def _pause_for_input(self):
        # Hearing is not a semantic cancellation. Hold the execution boundary
        # while a new utterance is classified; backchannels release this hold.
        self._input_serial += 1
        self._input_ready.clear()
        if self._judging and not self._judging.done():
            self._judging.cancel()

    def _invalidate_unresolved_input(self):
        self.dialogue.begin()
        if self.dialogue.pending:
            self.dialogue.pending.offered = False
        active = self._active_work
        if active and active is not asyncio.current_task() and not active.done():
            active.cancel()

    def _settle_duplicate_input(self):
        # A final replay contributes no new semantic turn. It may release a
        # hearing pause, but never overtake a genuinely active classifier/hold.
        if self._held is None and (not self._judging or self._judging.done()):
            self._input_ready.set()

    async def empty_input_stopped(self):
        """An ended input turn with no final text has no semantic permission.

        The aggregator emits no context frame for this case. Settle its hearing
        pause without treating it as a backchannel or overtaking a real pending
        classifier/continuation fragment.
        """
        if self._held is not None or (self._judging and not self._judging.done()):
            return False
        if self._input_ready.is_set():
            return False
        self._invalidate_unresolved_input()
        self._input_ready.set()
        await emit(self, "dialogue", reason="empty_input_stopped", operation="silent")
        return True

    def _schedule_dialogue(self, text, frame, direction, key=None, route=None):
        if not self.dialogue.accept(key):
            return False
        self._pause_for_input()
        if route is None:
            self._handler = asyncio.create_task(self._handle_turn(text, frame, direction, self._input_serial))
        else:
            self._handler = asyncio.create_task(
                self._handle_turn(text, frame, direction, self._input_serial, route=route))
        self._judging = self._handler
        self._open_turn = OpenTurn(text, self._handler)
        return True

    # -- late speech and the floor --------------------------------------------------

    def words_heard(self, text: str):
        """A transcript (interim or final) arrived: he is producing words."""
        self._last_words = time.monotonic()
        self._last_words_text = text or ""

    def _this_turn(self):
        turn = self._open_turn
        return turn if turn is not None and turn.task is asyncio.current_task() else None

    def _reply_starting(self):
        if BRIDGING.get():
            return
        turn = self._this_turn()
        if turn is not None:
            turn.replying = True

    def _merge_late(self, text: str):
        """Speech committed after a turn but before its reply started joins
        that turn: the pending ask is cancelled and re-run with both parts.
        Returns (merged text, route) or None when the words are a turn of
        their own. 25 Sep: "So what's on the docket? / What do I need to know?"
        got two answers, the second 11.6 s late; "Yeah, what are all the
        tasks? Can you expand the tranquility? / Director section so I can see
        what's." lost its second half."""
        import director_link
        prior = self._open_turn
        if (prior is None or prior.route is None or prior.replying or prior.task.done()
                or not director_link.director_default()):
            return None
        own = director_link.route_default(text, follow_up=True)
        if own is None or own[0] in {"mute", "call"}:
            return None     # "stop" is its own turn; a bare call opens a new one
        if own[0] == "hand" and (prior.route[0] != "hand" or own[1] != prior.route[1]):
            return None     # names someone else
        words = (prior.route[-1] + " " + own[-1]).strip()
        route = prior.route[:-1] + (words,)
        merged = (prior.text + " " + text).strip()
        prior.task.cancel()
        self._open_turn = None
        logger.info(f"merged late speech into the pending turn: {prior.text[:60]!r} + {text[:60]!r}")
        return merged, route

    async def _floor_ready(self):
        """Wait until his input is settled, but never hold a finished reply
        behind sound that carries no words: after FLOOR_WAIT_SECS with no new
        transcript (longer when his last words hold the floor), speak. A
        classifier or a held fragment still in flight is always waited for."""
        if self._input_ready.is_set() or self._floor_taken == self._input_serial:
            return
        start = time.monotonic()
        while not self._input_ready.is_set():
            now = time.monotonic()
            judging = (getattr(self, "_held", None) is not None
                       or (self._judging is not None and not self._judging.done()))
            needed = (max(FLOOR_WAIT_SECS, HOLD_SECS + 0.3) if holds_floor(self._last_words_text)
                      else FLOOR_WAIT_SECS)
            quiet = now - max(start, self._last_words)
            if not judging and quiet >= needed:
                self._floor_taken = self._input_serial
                logger.info(f"floor: reply waited {now - start:.1f} s behind hearing with no new "
                            f"words for {quiet:.1f} s; speaking")
                await emit(self, "dialogue", reason="floor_wait_capped", operation="speak",
                           ms=round((now - start) * 1000), quiet_ms=round(quiet * 1000))
                return
            wait = 0.1 if judging else max(0.05, needed - quiet)
            try:
                await asyncio.wait_for(self._input_ready.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        waited = time.monotonic() - start
        if waited >= 0.05:
            logger.info(f"floor: reply waited {waited:.1f} s for his turn to settle")

    async def _dialogue_turn(self, text, frame, direction, serial=None, route=None):
        from manager import INTENTS, note
        if serial is None:
            self._pause_for_input()
            serial = self._input_serial
        guard = TurnGuard(self.dialogue.epoch, (self.stage or {}).get("sessionId"))
        token = CURRENT_TURN.set(guard)
        took_floor = self._took_floor()  # consumed here, whatever the turn turns out to be
        t0 = time.monotonic()
        settled = False
        try:
            # DIRECTOR FIRST (24 Sep). What is Director's goes to Director before
            # any judgment: "Director, …", what needs him, what is ready, telling
            # an agent something. Who the voice is, it answers itself. Neither
            # costs a Jev call, and neither can be judged into "silent".
            import director_link
            # In Tranquility Base Director every utterance is Director's (25 Sep):
            # nothing is judged, nothing is left silent but an empty turn.
            default = director_link.director_default()
            last = getattr(self, "_last_answer", None)
            if default and last and director_link.is_echo(text, last[0], time.monotonic() - last[1]):
                settled = True
                self._judging = None
                self._input_ready.set()
                note("you", text, "echo of the card, ignored")
                return
            if default and took_floor:
                from barge_in import is_hold
                if is_hold(text):
                    # "Stop." "Wait." "Hold on." said over the voice: it stopped;
                    # nothing restarts it and nothing is asked. The conversation
                    # stays open, so what he says next is Director's.
                    guard.epoch = self.dialogue.begin()
                    settled = True
                    self._judging = None
                    self._input_ready.set()
                    self._follow_up_until = time.monotonic() + director_link.FOLLOW_UP_SECS
                    note("you", text, "held the floor; Director stopped")
                    await emit(self, "listening", reason="barge_in_hold", text=text[:120])
                    return
            now = time.monotonic()
            held = getattr(self, "_held_fragment", None)
            if default and held and now - held[1] < director_link.HOLD_FRAGMENT_SECS:
                # the rest of a sentence Director held as unfinished
                text = f"{held[0]} {text}"
                self._held_fragment = None
            called = getattr(self, "_called", None)
            follow_up = (now < getattr(self, "_follow_up_until", 0.0)
                         or bool(called and now - called[1] < director_link.CALL_WINDOW))
            if route is not None:
                routed = route      # a merged turn keeps the first part's addressee
            else:
                routed = (director_link.route_default(text, follow_up=follow_up) if default
                          else director_link.route(text))
            if default and route is None and routed is not None and routed[0] != "call":
                routed = director_link.answer_call(routed, called, now)
                self._called = None
            if routed is None and default:
                # Not for anyone: the room's talk. No event says it was
                # addressed, and Director never hears it (tb-address-gate).
                settled = True
                self._judging = None
                self._input_ready.set()
                await emit(self, "listening", text=text[:120])
                note("you", text, "not addressed; ignored")
                logger.info(f"gate: ignored {text[:80]!r}: no name, and the conversation window "
                            f"{'closed ' + str(round(now - getattr(self, '_follow_up_until', 0.0), 1)) + ' s ago' if getattr(self, '_follow_up_until', 0.0) else 'never opened'}")
                return
            if routed is not None:
                # Said over Director's voice: what it was saying is superseded,
                # not restarted once this turn settles. _say's one retry is for
                # a turn that turns out not to be Director's (routed is None
                # above), which then resumes the line (research R10).
                if took_floor:
                    guard.epoch = self.dialogue.begin()
                settled = True
                self._judging = None
                self._input_ready.set()
                turn = self._this_turn()
                if turn is not None and routed[0] in {"ask", "hand"}:
                    turn.route = routed
                note("you", text, "understood")
                self.addressed += 1
                await emit(self, "addressed", intent="director_" + routed[0], text=text[:120])
                if routed[0] == "identity":
                    await self._say(director_link.IDENTITY_LINE, response_mode="receipt")
                elif routed[0] == "call":
                    # "Director." alone: the cue says it is listening, and the
                    # next words are the question. A placeholder says so now.
                    self._called = (routed[1], time.monotonic())
                    hand = director_link.hand_named(routed[1]) or {}
                    if routed[1] != "Director" and not hand.get("session"):
                        await self._relay_hand(routed[1], "", text)
                    else:
                        await self._earcon("listening")
                elif routed[0] == "mute":
                    await self.broadcast_interruption()
                    await self._do_mute("", frame, direction)
                elif routed[0] == "hand":
                    await self._relay_hand(routed[1], routed[2], text)
                elif director_link.hand_named("Director"):
                    await self._relay_hand("Director", routed[1], text)
                else:
                    await self._relay_director(routed[1])
                return
            targets = await self._targets()
            self._require_current()
            self.dialogue.sync(guard.stage, targets)
            state = judgment_state(text, self.dialogue.snapshot())
            answers = await self._jev.ask(state, build_questions(INTENTS, targets))
            self._require_current()
            if serial != self._input_serial:
                raise asyncio.CancelledError("newer transcript being judged")
            # Preview without mutations to decide whether this utterance should
            # supersede ongoing work. Silence never destroys an active answer.
            preview = deepcopy(self.dialogue).decide(text, answers, self.dialogue.epoch)
            previous_pending = self.dialogue.pending
            if preview.op != "silent":
                guard.epoch = self.dialogue.begin()
                active = self._active_work
                if active and active is not asyncio.current_task() and not active.done():
                    active.cancel()
                self._active_work = asyncio.current_task()
                decision = self.dialogue.decide(text, answers, guard.epoch)
            else:
                decision = preview
            self._remember_transition(previous_pending, decision)
            settled = True
            self._judging = None
            self._input_ready.set()
            self.dialogue.remember(text, chosen(answers, "act"), decision)
            milliseconds = round((time.monotonic() - t0) * 1000)
            fields = dict(intent=chosen(answers, "act"), reason=decision.reason,
                          response=decision.response, ms=milliseconds, epoch=guard.epoch)
            await emit(self, "dialogue", **fields, operation=decision.op,
                       target=decision.target, pending=decision.pending_id, answers=answers)
            await emit(self, "listening" if decision.op == "silent" else "addressed",
                       **fields, text=text[:120])
            note("you", text, "silent" if decision.op == "silent" else "understood")
            if decision.op != "silent":
                self.addressed += 1
            await self._execute_decision(decision, frame, direction)
        finally:
            if serial == self._input_serial:
                if not settled:
                    # An unclassified interruption might be a cancellation. Do
                    # not reopen an old dispatch boundary as if it were an ack.
                    self._invalidate_unresolved_input()
                self._input_ready.set()
            CURRENT_TURN.reset(token)

    async def _execute_decision(self, decision, frame, direction):
        self._require_current()
        if decision.op == "silent":
            return
        if decision.op == "mute":
            # Delegate audio interruption to the existing framework/native paths.
            await self.broadcast_interruption()
            await self._do_mute("", frame, direction)
        elif decision.op in {"say", "clarify"}:
            if (self.dialogue.stage and (decision.reason == "nothing_to_cancel"
                    or decision.reason == "cannot_undo" and self.dialogue.last_information)):
                canceled = self.memory.cancel_question(self.dialogue.stage, f"dialogue:{decision.epoch}:cancel")
                if canceled:
                    decision.text = "Canceled the unanswered question."
                    if decision.reason == "cannot_undo":
                        decision.text += " The earlier work request is unchanged."
                    self.dialogue.last_information = None
                    self.memory.remember_decision(self.dialogue.stage, "canceled",
                                                  f"question:{canceled.id}", canceled.text)
                    await emit(self, "memory", reason="question_canceled", question=canceled.id,
                               target=canceled.target)
            # Offering happens after delivery to speech, not at proposal creation.
            offered = await self._say(decision.text, response_mode=decision.response)
            self._require_current()
            if offered is True:
                self.dialogue.mark_offered(decision.pending_id)
        elif decision.op == "dispatch":
            await self._dialogue_dispatch(decision)
        elif decision.op == "answer":
            await self._dialogue_answer(decision, frame, direction)

    async def _dialogue_dispatch(self, decision):
        from manager import TBASE
        from tools import _run
        await self._input_ready.wait()
        self._require_current()
        targets = await self._targets()
        await self._input_ready.wait()
        self._require_current()
        self.dialogue.sync((self.stage or {}).get("sessionId"), targets)
        if (decision.route == "start_agent"
                and self.dialogue.targets.get(decision.target, {}).get("cwd") != decision.text):
            self.dialogue.pending = None
            await self._say("That launch directory changed. Please request it again.")
            return
        action = self.dialogue.commit(decision)
        if action is None:
            await self._say("That request is no longer ready to send. Please state it again.")
            return
        # Crossing this boundary is irreversible. A new user turn can cancel its
        # receipt, but cannot claim the subprocess never ran or replay it.
        async def deliver():
            try:
                if decision.route == "start_agent":
                    code, out = await _run(TBASE, "new", action.text, "--wait-live", timeout=60)
                    action.status = "sent" if code == 0 and "registered:" in out else "unknown"
                else:
                    code, _ = await _run(TBASE, "send", action.target, action.text)
                    action.status = {0: "sent", 2: "not_sent", 3: "waiting",
                                     4: "not_sent", 5: "failed"}.get(code, "unknown")
            except Exception:
                code, action.status = -1, "unknown"
            await emit(None, "dialogue", reason="delivery_result", action=action.id,
                       target=action.target, status=action.status, exit=code)
            return action.status
        task = asyncio.create_task(deliver())
        self._deliveries.add(task)
        task.add_done_callback(self._deliveries.discard)
        status = await asyncio.shield(task)
        self._require_current()
        if status == "sent":
            await self._earcon("dispatched")
        line = {
            "sent": "Stop request sent." if decision.route == "stop_agent" else "Sent.",
            "not_sent": "The request was not sent.",
            "waiting": "Delivery is waiting. It has not been confirmed sent.",
            "failed": "Delivery failed. I have not retried.",
            "unknown": "Delivery status is unknown. I will not retry automatically.",
        }[status]
        if decision.route == "start_agent" and status == "sent":
            line = "New agent registered."
        await self._say(line, response_mode="receipt")

    async def _dialogue_answer(self, decision, frame, direction):
        from manager import RUNG_FOR
        target = decision.target
        route = decision.route
        if route in {"fleet_inventory", "fleet_count"}:
            await self._fleet_inventory(include_names=route == "fleet_inventory")
            return
        if route == "manager_status":
            await self._say("Yes. I received your message.", response_mode="receipt")
            return
        if route == "manager_question":
            await self._manager_question(decision.text)
            return
        if route == "conversation_resume":
            await self._resume_conversation(target)
            return
        question = self._memory_question(decision) if route not in {"invite_next", "teach", "speak", "summarize_recent"} else None
        if decision.recorded_text:
            observation = self.memory.observe(
                f"dialogue:{decision.source}:{decision.epoch}", target or "manager", {"value": decision.recorded_text})
            if decision.response == "exact_command":
                try:
                    value = ExactValue("command", decision.recorded_text)
                except ValueError:
                    await self._say("That recorded request cannot be read as one exact command.")
                    return
                await self._speak_evidence(value.value, observation, question=question, exact=value)
            else:
                prefix = {"last_sent_request": "Sent request: ",
                          "recorded_command": "Recorded command: "}.get(decision.reason, "Unsent request: ")
                await self._speak_evidence(prefix + decision.recorded_text, observation, question=question, response_mode=decision.response)
            return
        if route == "invite_next":
            await self._do_invite_next(decision.text, frame, direction)
            return
        if route in {"teach", "speak"}:
            await getattr(self, "_do_" + route)(decision.text, frame, direction)
            return
        if route == "summarize_recent":
            briefs = await self._recent_briefs()
            self._require_current()
            answer = await self._brain.plain(
                "Summarize these recorded briefs without claiming any actions.", [str(b) for b in briefs])
            self._require_current()
            await self._say(answer, response_mode="summary")
            return
        if not target or target not in self.dialogue.targets:
            await self._say("That agent is no longer available. Ask me to list the live agents.", response_mode="receipt")
            return
        if decision.response.startswith("exact_"):
            await self._exact_value(decision.response.removeprefix("exact_"), target, question=question)
            return
        brief = await self._brief(target)
        self._require_current()
        if not brief or brief.get("sessionId") != target:
            await self._say("That session has no matching notes to answer from.")
            return
        mode = "detail" if decision.response == "detail" else "summary"
        kind = RUNG_FOR.get(route)
        observation = self._observe_brief(target, brief)
        update = kind == "findings" and mode == "summary"
        if (update and not self._explicit_repeat(decision.text)
                and not self.memory.should_update(observation, self._critical_update(brief))):
            await emit(self, "memory", reason="unchanged_update_suppressed", target=target,
                       source=observation.source_id)
            await self._say("No new recorded update.", response_mode="receipt")
            return
        rung = next((r for r in brief.get("rungs", []) if r.get("kind") == kind), None)
        if rung and mode != "detail":
            answer = rung["spoken"]
        else:
            answer = await self._brain.answer(decision.text, brief, self._recent, mode=mode)
        self._require_current()
        delivered = await self._speak_evidence(answer or "The notes don't say.", observation,
                                               question=question, response_mode=mode, update=update)
        self._require_current()
        # Only a recorded proposal that was actually presented is a referent.
        if delivered is True and kind == "solution" and rung and brief.get("proposal"):
            self.dialogue.offer_proposal(brief["proposal"], target)

    async def _close_dialogue(self, frame=None, direction=None):
        self.dialogue.begin()
        if self._held_task:
            self._held_task.cancel()
        self._input_ready.set()
        for task in (self._handler, self._active_work, self._judging):
            if task and not task.done():
                task.cancel()
        # A committed send may take tens of seconds to settle. Audio teardown
        # must not wait behind that subprocess: forward the lifecycle frame once
        # after invalidating local work, then observe the irreversible result.
        if frame is not None:
            await self.push_frame(frame, direction)
        # Observe already committed sends; never turn shutdown into a retry.
        if self._deliveries:
            await asyncio.gather(*tuple(self._deliveries), return_exceptions=True)
