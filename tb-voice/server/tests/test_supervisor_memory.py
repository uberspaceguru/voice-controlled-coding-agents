import os
import tempfile
import unittest
from pathlib import Path

from supervisor_memory import JournalError, SupervisorJournal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.journal = SupervisorJournal(Path(self.tmp.name) / "state", clock=lambda: self.now)

    def test_receipts_survive_restart_without_authorizing_or_claiming_completion(self):
        self.journal.event("dispatch", request_id="one", target="alpha", text="Run tests", status="sent")
        reopened = SupervisorJournal(self.journal.directory, clock=lambda: self.now + 100)
        history = reopened.context()["history"]
        self.assertEqual(history[0]["status"], "sent")
        self.assertEqual(history[0]["age_seconds"], 100)
        self.assertNotIn("pending", reopened.context())
        with self.assertRaises(JournalError):
            self.journal.event("dispatch", request_id="one", status="completed")

    def test_generated_interrupted_and_output_completion_are_distinct_and_idempotent(self):
        for status in ("generated", "interrupted_or_unknown", "output_complete", "output_complete"):
            self.journal.event("response", request_id="one", text="Fact", status=status)
        self.assertEqual([r["status"] for r in self.journal.context()["history"]],
                         ["generated", "interrupted_or_unknown", "output_complete"])
        self.assertNotIn("acknowledged", self.journal.path.read_text())

    def test_absent_agent_is_not_marked_complete(self):
        candidate = {"session_id": "alpha", "name": "Alpha"}
        source = {"id": "a", "session_id": "alpha", "kind": "brief", "data": {"goal": "Tests"}}
        self.journal.observe([candidate], [source])
        self.now += 10
        self.journal.observe([], [])
        row = self.journal.context()["agents"]["alpha"]
        self.assertEqual(row["presence"], "not_in_latest_scan")
        self.assertEqual(row["last_seen_at"], 1000)

    def test_unchanged_evidence_does_not_reset_changed_timestamp(self):
        candidate = {"session_id": "alpha"}
        source = {"id": "a", "session_id": "alpha", "kind": "brief", "data": {"goal": "Tests"}}
        self.journal.observe([candidate], [source])
        self.now += 20
        self.journal.observe([candidate], [dict(source, id="new_observation")])
        row = self.journal.context()["agents"]["alpha"]
        self.assertEqual(row["changed_at"], 1000)
        self.assertEqual(row["last_seen_at"], 1020)

    def test_inferences_keep_evidence_and_do_not_become_observations(self):
        source = {"id": "a", "session_id": "alpha", "kind": "brief", "data": {"goal": "Tests"}}
        self.journal.remember_notes([{"session_id": "alpha", "summary": "Working on tests", "evidence_ids": ["a"]}], [source])
        self.assertEqual(self.journal.context()["notes"][0]["kind"], "model_inference")
        with self.assertRaises(JournalError):
            self.journal.remember_notes([{"session_id": "beta", "summary": "Wrong agent", "evidence_ids": ["a"]}], [source])

    def test_late_old_scan_cannot_overwrite_newer_presence(self):
        def fleet(at):
            return {"id": str(at), "kind": "fleet", "session_id": None, "observed_at": at, "data": {}}
        self.journal.observe([{"session_id": "alpha"}], [fleet(200)])
        self.journal.observe([], [fleet(100)])
        self.assertEqual(self.journal.context()["agents"]["alpha"]["presence"], "observed")
        self.journal.observe([], [fleet(300)])
        self.assertEqual(self.journal.context()["agents"]["alpha"]["presence"], "not_in_latest_scan")

    def test_corrupt_journal_is_not_silently_overwritten(self):
        self.journal.path.write_text("broken")
        with self.assertRaises(JournalError):
            self.journal.event("request", request_id="one")
        self.assertEqual(self.journal.path.read_text(), "broken")

    def test_history_is_bounded_to_provider_contract_and_files_are_private(self):
        for i in range(50):
            self.journal.event("request", request_id=str(i), status="understood")
        self.assertEqual(len(self.journal.context()["history"]), 40)
        self.assertEqual(os.stat(self.journal.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.journal.directory).st_mode & 0o777, 0o700)

    def test_symlink_journal_is_refused(self):
        target = Path(self.tmp.name) / "other"
        target.write_text("{}")
        self.journal.path.symlink_to(target)
        with self.assertRaises(JournalError):
            self.journal.context()
