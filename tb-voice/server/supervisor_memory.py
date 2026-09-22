"""Durable observations and receipts for the supervisor, never executable authority.

Pending confirmations deliberately stay in Dialogue's short-lived memory. Loading
this journal cannot authorize or replay a request. Inferred notes retain sources;
output completion is distinct from the user's acknowledgment.
"""

import fcntl
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class JournalError(RuntimeError):
    pass


class SupervisorJournal:
    def __init__(self, directory, *, clock=time.time):
        self.directory = Path(directory)
        self.clock = clock
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_uid != os.getuid():
            raise JournalError("Supervisor state must be an owned directory")
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "journal.json"

    @contextmanager
    def _locked(self):
        fd = os.open(self.directory / "journal.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "agents": {}, "events": [], "notes": []}
        if self.path.is_symlink() or self.path.stat().st_size > 2_000_000:
            raise JournalError("Supervisor journal is not a bounded regular file")
        try:
            data = json.loads(self.path.read_text())
            if (data.get("version") != 1 or not isinstance(data.get("agents"), dict)
                    or not isinstance(data.get("events"), list) or not isinstance(data.get("notes"), list)):
                raise ValueError("invalid journal")
            return data
        except (OSError, ValueError, AttributeError) as error:
            raise JournalError("Supervisor journal could not be read") from error

    def _save(self, data):
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload.encode()) > 2_000_000:
            raise JournalError("Supervisor journal exceeds its size bound")
        temporary = self.directory / (".journal-" + uuid.uuid4().hex)
        try:
            with open(temporary, "x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def event(self, kind, *, request_id, target=None, text="", status=None, evidence_ids=()):
        """Idempotent receipt; no model-supplied action or success state is accepted."""
        if kind not in {"request", "dispatch", "response", "decision", "failure"}:
            raise JournalError("Unsupported supervisor event kind")
        if status not in {None, "understood", "dispatching", "sent", "not_sent", "waiting",
                          "failed", "unknown", "output_complete", "interrupted_or_unknown",
                          "generated", "canceled", "held", "superseded", "clarify"}:
            raise JournalError("Unsupported supervisor event status")
        if not isinstance(request_id, str) or not request_id or len(text) > 8192:
            raise JournalError("Invalid supervisor event")
        entry = {"id": f"{request_id}:{kind}:{status}", "request_id": request_id,
                 "kind": kind, "target": target, "text": text, "status": status,
                 "observed_at": self.clock(), "evidence_ids": list(evidence_ids)}
        with self._locked():
            data = self._load()
            if any(row["id"] == entry["id"] for row in data["events"]):
                return
            data["events"] = (data["events"] + [entry])[-256:]
            self._save(data)

    def observe(self, candidates, observations):
        """Replace current witnesses, retain vanished agents as absent, not finished."""
        now = self.clock()
        captured_at = next((row["observed_at"] for row in observations if row.get("kind") == "fleet"), now)
        with self._locked():
            data = self._load()
            # A canceled to_thread writer may settle after a newer scan. The
            # file lock serializes writes; source time supplies their ordering.
            if captured_at < data.get("snapshot_at", 0):
                return
            data["snapshot_at"] = captured_at
            current = {row["session_id"]: row for row in candidates}
            for sid, entry in data["agents"].items():
                if sid not in current:
                    entry["presence"] = "not_in_latest_scan"
            for sid, row in current.items():
                old = data["agents"].get(sid, {})
                sources = [o for o in observations if o.get("session_id") == sid]
                signature = hashlib.sha256(json.dumps(
                    [{"kind": o["kind"], "data": o["data"]} for o in sources],
                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                data["agents"][sid] = {
                    "candidate": row, "presence": "observed", "last_seen_at": now,
                    "first_seen_at": old.get("first_seen_at", now),
                    "changed_at": old.get("changed_at", now) if old.get("signature") == signature else now,
                    "signature": signature, "observations": sources,
                }
            # Keep recently seen agents; absence never becomes completion.
            data["agents"] = dict(sorted(data["agents"].items(),
                                          key=lambda pair: pair[1]["last_seen_at"], reverse=True)[:128])
            self._save(data)

    def context(self):
        with self._locked():
            data = self._load()
        now = self.clock()
        events = [dict(e, age_seconds=max(0, now - e["observed_at"])) for e in data["events"][-40:]]
        return {"history": events, "agents": data["agents"], "notes": data["notes"]}

    def remember_notes(self, notes, observations):
        by_id = {o["id"]: o for o in observations}
        kept = []
        for note in notes:
            sid = note.get("session_id")
            refs = note.get("evidence_ids", [])
            if (not refs or any(ref not in by_id for ref in refs)
                    or any(by_id[ref].get("session_id") not in {None, sid} for ref in refs)):
                raise JournalError("Work note has no matching source evidence")
            kept.append({"session_id": sid, "summary": str(note.get("summary", ""))[:1200],
                         "evidence_ids": refs, "kind": "model_inference", "observed_at": self.clock()})
        with self._locked():
            data = self._load()
            changed = {n["session_id"] for n in kept}
            data["notes"] = ([n for n in data["notes"] if n["session_id"] not in changed] + kept)[-128:]
            self._save(data)
