"""Persistent supervisor over the existing voice policy and verified fleet doors.

Jev and Dialogue still own speech acts and authorization. The persistent reasoning
session receives observations and actual receipts, not a terminal or arbitrary
shell tool. Ordinary sends keep their short path and literal source text.
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from events import emit
from supervisor_memory import JournalError, SupervisorJournal


def state_directory():
    root = os.getenv("VOICE_DISPATCH_SUPPORT_DIR")
    support = Path(root) if root else Path.home() / "Library/Application Support/VoiceDispatch"
    return Path(os.getenv("TB_SUPERVISOR_STATE_DIR", str(support / "supervisor")))


def candidate_rows(snapshot, targets):
    """Only uniquely associated live agents enter the manager's candidate set."""
    known = {row["sessionId"]: row for row in targets}
    choices = {}
    for pane in snapshot.panes:
        if pane["dead"] or pane["identityStatus"] != "verified" or len(pane["agents"]) != 1:
            continue
        for agent in pane["agents"]:
            sid = agent["sessionId"]
            row = known.get(sid, {})
            item = {"session_id": sid, "name": row.get("name") or agent.get("name")
                    or f"{pane['sessionName']} / {pane['windowName']} / {pane['paneId']}",
                    "cwd": pane.get("cwd", ""), "harness": agent["harness"],
                    "status": row.get("status") or agent.get("status") or "unknown",
                    "socket_path": pane["socketPath"], "pane_id": pane["paneId"]}
            choices.setdefault(sid, []).append(item)
    return [items[0] for items in choices.values() if len(items) == 1]


def bounded_context(context, limit=240_000):
    """Keep the request and current identities ahead of old conversational detail.

    Every omission is visible to the model. The final engine limit is stricter
    than a count bound alone, especially with long source summaries.
    """
    fleet = context["observations"][0]["data"]
    omitted = fleet.setdefault("context_omissions", {})
    def oversized():
        return len(json.dumps(context, ensure_ascii=False).encode()) > limit
    def dropped(kind):
        omitted[kind] = omitted.get(kind, 0) + 1
    for key in ("inferred_notes", "history"):
        while context[key] and oversized():
            context[key].pop(0)
            dropped(key)
    # Read order already prioritizes the stage and waiting work.
    for row in reversed(context["observations"][1:]):
        if not oversized():
            break
        if row["kind"] in {"stored_brief", "notes_unavailable"}:
            context["observations"].remove(row)
            dropped("source_briefs")
    while len(context["candidates"]) > 1 and oversized():
        row = context["candidates"].pop()
        sid = row["session_id"]
        context["observations"] = [o for o in context["observations"] if o["session_id"] != sid]
        dropped("candidate_details")
    return context


class SupervisorManagerMixin:
    def _init_supervisor(self):
        self._supervisor = None
        self._supervisor_journal = None
        self._supervisor_watch = None
        self._supervisor_refresh_lock = asyncio.Lock()
        if os.getenv("TB_MANAGER_BACKEND", "dialogue") != "codex":
            return
        from supervisor_session import SupervisorSession
        directory = state_directory()
        self._supervisor_journal = SupervisorJournal(directory)
        self._supervisor = SupervisorSession(directory / "conversation")

    async def _supervisor_event(self, kind, **fields):
        journal = getattr(self, "_supervisor_journal", None)
        if journal is not None:
            await asyncio.to_thread(journal.event, kind, **fields)

    async def _supervisor_observe(self):
        """Bounded source reads; no inference, transcript scanning, or execution."""
        async with self._supervisor_refresh_lock:
            snapshot = await self._tmux_fleet()
            targets = await self._targets()
            candidates = candidate_rows(snapshot, targets)
            unique_ids = {c["session_id"] for c in candidates}
            observations = [{"id": f"fleet:{snapshot.snapshot_id}", "kind": "fleet",
                             "session_id": None, "observed_at": snapshot.captured_at,
                             "data": {"partial": snapshot.partial, "warnings": list(snapshot.warnings),
                                      "pane_count": len(snapshot.panes), "verified_agents": len(candidates),
                                      "ambiguous_agent_locations": [
                                          {"session_id": a["sessionId"], "socket_path": p["socketPath"],
                                           "pane_id": p["paneId"], "session_name": p["sessionName"]}
                                          for p in snapshot.panes for a in p["agents"]
                                          if a["sessionId"] not in unique_ids][:128],
                                      "unidentified_panes": [
                                          {k: pane.get(k) for k in ("id", "sessionName", "windowName", "paneId", "cwd", "command")}
                                          for pane in snapshot.panes if not pane["agents"]][:64]}}]
            # The complete inventory remains available; brief retrieval has an
            # explicit cap. Prefer the selected agent and then known waiting work.
            by_id = {row["sessionId"]: row for row in targets}
            stage_id = (self.stage or {}).get("sessionId")
            ordered = sorted(candidates, key=lambda c: (
                c["session_id"] != stage_id, not by_id.get(c["session_id"], {}).get("waiting", False), c["session_id"]))
            semaphore = asyncio.Semaphore(4)

            async def read_brief(candidate):
                sid = candidate["session_id"]
                async with semaphore:
                    brief = await self._brief(sid)
                if not brief or brief.get("sessionId") != sid:
                    return {"id": f"notes-unavailable:{snapshot.snapshot_id}:{sid}",
                            "kind": "notes_unavailable", "session_id": sid,
                            "observed_at": time.time(), "data": {"reason": "no_matching_stored_brief"}}
                # Whitelist existing summaries; no transcript paths, keys, full
                # scrollback, or unbounded tool output enter the model context.
                fields = {k: str(brief[k])[:800] for k in
                          ("goal", "recap", "proposal", "findings", "solution", "why")
                          if brief.get(k) is not None}
                recorded = brief.get("recordedAtMs")
                fields["recorded_at"] = recorded / 1000 if isinstance(recorded, (int, float)) and recorded > 0 else None
                fields["freshness"] = "recorded_turn_not_live_progress"
                return {"id": f"brief:{sid}:{brief.get('eventId', 'unknown')}",
                        "kind": "stored_brief", "session_id": sid,
                        "observed_at": time.time(), "data": fields}

            observations += await asyncio.gather(*(read_brief(c) for c in ordered[:32]))
            observations[0]["data"]["briefs_omitted_by_bound"] = max(0, len(candidates) - 32)
            for candidate in candidates:
                observations.append({"id": f"agent:{snapshot.snapshot_id}:{candidate['session_id']}",
                                     "kind": "live_identity", "session_id": candidate["session_id"],
                                     "observed_at": snapshot.captured_at, "data": candidate})
            await asyncio.to_thread(self._supervisor_journal.observe, candidates, observations)
            return candidates, observations

    def _start_supervisor_watch(self):
        if getattr(self, "_supervisor", None) is not None and not self._supervisor_watch:
            self._supervisor_watch = self.create_task(self._watch_supervisor(), name="fleet_observations")

    async def _watch_supervisor(self):
        while True:
            try:
                await self._supervisor_observe()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed read does not replace the last evidence with an empty
                # fleet or announce agents finished. Fresh turns retry explicitly.
                await emit(self, "supervisor", reason="observation_read_failed")
            await asyncio.sleep(20)

    async def _stop_supervisor(self):
        watch = getattr(self, "_supervisor_watch", None)
        if watch:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
            self._supervisor_watch = None
        supervisor = getattr(self, "_supervisor", None)
        if supervisor:
            await supervisor.cancel()

    async def _supervisor_answer(self, decision):
        try:
            await self._supervisor_answer_inner(decision)
        except (JournalError, OSError):
            self._require_current()
            await emit(self, "supervisor", reason="journal_unavailable")
            await self._say("I couldn't save the manager's conversation record. Nothing was sent.", response_mode="receipt")

    async def _supervisor_answer_inner(self, decision):
        """A read-only model turn. The existing dispatch path owns all writes."""
        from supervisor_session import SupervisorError
        request_id = uuid.uuid4().hex
        await self._supervisor_event("request", request_id=request_id, target=decision.target,
                                     text=decision.text, status="understood")
        candidates, observations = await self._supervisor_observe()
        await self._input_ready.wait()
        self._require_current()
        memory = await asyncio.to_thread(self._supervisor_journal.context)
        # Historical records are context, never current observations or permission.
        # Do not resend every historical source payload on every turn.
        work_memory = {sid: {k: row[k] for k in ("presence", "last_seen_at", "changed_at")}
                       for sid, row in memory["agents"].items()}
        if len(candidates) > 100:
            omitted_count = len(candidates) - 100
            candidates = sorted(candidates, key=lambda row: row["session_id"] != decision.target)[:100]
            selected = {row["session_id"] for row in candidates}
            observations = [row for row in observations if row["session_id"] is None or row["session_id"] in selected]
            observations[0]["data"]["candidates_omitted_by_bound"] = omitted_count
        context = {"request_id": request_id, "user_text": decision.text,
                   "observations": observations, "candidates": candidates,
                   "message_sources": [], "route": decision.route, "source": "utterance",
                   "history": memory["history"], "work_memory": work_memory,
                   "inferred_notes": memory["notes"], "stage": (self.stage or {}).get("sessionId"),
                   "allowed_operations": ["answer", "clarify"], "allowed_target_ids": []}
        context = bounded_context(context)
        started = time.monotonic()
        try:
            plan = await self._supervisor.turn(context)
        except SupervisorError as error:
            self._require_current()
            await self._supervisor_event("failure", request_id=request_id, status="failed")
            await emit(self, "supervisor", reason="reasoning_failed", code=error.code)
            await self._say("The manager couldn't finish that answer. Nothing was sent.", response_mode="receipt")
            return
        self._require_current()
        # Defense in depth even if an injected test/provider bypasses the engine.
        if plan.operation not in {"answer", "clarify"}:
            await emit(self, "supervisor", reason="ungranted_operation_rejected")
            await self._say("I couldn't validate that answer. Nothing was sent.", response_mode="receipt")
            return
        await emit(self, "supervisor", reason="reasoning_complete", ms=round((time.monotonic() - started) * 1000),
                   operation=plan.operation, evidence=list(plan.evidence_ids))
        await self._supervisor_event("response", request_id=request_id, text=plan.reply,
                                     status="generated", evidence_ids=plan.evidence_ids)
        delivered = False
        prior_delivery = getattr(self, "_last_delivery", None)
        try:
            delivered = await self._say(plan.reply, response_mode="detail" if decision.response == "detail" else "summary")
        finally:
            delivery = getattr(self, "_last_delivery", None)
            output = (getattr(delivery, "generated_text", None) if delivery is not prior_delivery else None) or plan.reply
            await self._supervisor_event("response", request_id=request_id, text=output,
                                         status="output_complete" if delivered is True else "interrupted_or_unknown",
                                         evidence_ids=plan.evidence_ids)
        notes = getattr(plan, "work_notes", ())
        if notes:
            await asyncio.to_thread(self._supervisor_journal.remember_notes, notes, observations)
