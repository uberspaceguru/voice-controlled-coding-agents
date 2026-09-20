"""Evidence-based conversational memory at existing read and speech boundaries."""

import asyncio
import re

from conversation_memory import Memory
from events import emit


class MemoryManagerMixin:
    def _init_memory(self):
        self.memory = Memory()

    def _memory_question(self, decision):
        if not decision.target or decision.target not in self.dialogue.targets:
            return None
        kind = decision.response.removeprefix("exact_") if decision.response.startswith("exact_") else "information"
        return self.memory.ask(decision.text, decision.target, kind)

    def _remember_transition(self, previous, decision):
        pending = self.dialogue.pending
        source = f"dialogue:{decision.epoch}:{decision.reason}"
        if previous and previous is not pending and previous.target:
            status = "canceled" if decision.reason in {"pending_canceled", "uncertain_repair_withdrawn"} else "superseded"
            if decision.op != "dispatch":
                self.memory.remember_decision(previous.target, status, source, previous.text)
        if pending and pending.target and decision.op != "silent":
            status = "held" if pending.status == "held" else "proposed"
            self.memory.remember_decision(pending.target, status, source, pending.text)

    async def _exact_unavailable(self, question, target, kind, message):
        if question is None:
            await self._say(message)
            return
        observation = self.memory.observe(f"missing:{target}:{kind}", target, {"kind": kind, "available": False})
        self.memory.claim_repair(question)
        await self._speak_evidence(message, observation, question=question, response_mode="clarification")

    def _observe_brief(self, target, brief):
        # Stable source contents, excluding the read time and event ID. A new
        # event carrying identical content is not a meaningful change.
        payload = {key: brief.get(key) for key in (
            "recap", "proposal", "goal", "findings", "solution", "why", "rungs", "lastAssistantMessage",
        )}
        return self.memory.observe(f"brief:{target}:{brief.get('eventId', 'snapshot')}", target, payload)

    def _critical_update(self, brief):
        # The brief API does not expose an authoritative error/decision flag.
        # Conservatively retain every proposal and any explicit failure wording.
        # This may repeat benign proposals; it cannot silently hide a decision.
        if brief.get("proposal"):
            return True
        text = " ".join(str(brief.get(key) or "") for key in ("recap", "findings", "lastAssistantMessage"))
        return bool(re.search(r"\b(?:error|failed|failure|blocked|waiting|decision|approval)\b", text, re.I))

    @staticmethod
    def _explicit_repeat(text):
        # Only complete, explicit repeat requests bypass change suppression here.
        return bool(re.fullmatch(
            r"(?:tranquility[, :]*)?(?:please )?(?:repeat (?:that|the|the last) (?:update|summary)|"
            r"(?:say|read) (?:that|the update|the summary) again)[.!?\s]*", text.strip(), re.I))

    async def _speak_evidence(self, text, observation, *, question=None, exact=None,
                              response_mode="summary", update=False):
        """Record actual synthesized/output evidence; one shared repair budget.

        Never use a True mock/legacy return or queued text alone as completion.
        Memory owns the one retry here; _say's own retry is disabled so budgets
        cannot multiply. Generic answer relevance is intentionally unverified.
        """
        for attempt in range(2):
            before = self._last_delivery
            try:
                result = await self._say(text, exact=exact, response_mode=response_mode,
                                         retry_interrupted=False)
                self._require_current()
            except asyncio.CancelledError:
                if question:
                    self.memory.answer(question, observation, "", "interrupted")
                await emit(self, "memory", reason="answer_interrupted", source=observation.source_id,
                           question=question.id if question else None, target=observation.target)
                raise
            delivery = self._last_delivery
            correlated = delivery is not None and delivery is not before and delivery.text == text
            status = delivery.status if correlated else "unknown"
            actual = delivery.generated_text if correlated else ""
            delivery_id = delivery.id if correlated else None
            if result is not True and status == "output_complete":
                status = "unknown"
            resolved = False
            if question:
                resolved = self.memory.answer(question, observation, actual, status,
                                              expected=exact.value if exact else None,
                                              delivery_id=delivery_id)
            if update:
                self.memory.delivered_update(observation, actual, status, delivery_id=delivery_id)
            await emit(self, "memory", reason="answer_resolved" if resolved else "answer_unresolved",
                       question=question.id if question else None, source=observation.source_id,
                       delivery=delivery_id, status=status, target=observation.target)
            repairable = status == "interrupted" or (exact is not None and question is not None and status == "output_complete" and not resolved)
            if not repairable or attempt or not self._current():
                return status == "output_complete" and bool(actual) and (exact is None or question is None or resolved)
            if question and not self.memory.claim_repair(question):
                return False
            await emit(self, "memory", reason="bounded_answer_repair", question=question.id if question else None)
            await self._input_ready.wait()
            self._require_current()
        return False

    async def _resume_conversation(self, target):
        self.dialogue.expire()
        row = self.dialogue.targets.get(target)
        if not row:
            await self._say("Which agent should we return to?", response_mode="clarification")
            return
        label = str(row.get("name") or row.get("project") or target)[:200]
        answer = self.memory.resume(target, label, self.dialogue.pending, self.dialogue.last_action)
        goal = row.get("goal")
        if isinstance(goal, str) and goal.strip():
            answer += " Recorded task: " + goal[:300]
        await emit(self, "memory", reason="conversation_resumed", target=target)
        await self._say(answer, response_mode="detail")
