import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from supervisor_session import (
    MAX_REQUESTS,
    MAX_TURN_DIRECTORIES,
    SupervisorError,
    SupervisorSession,
)


class SupervisorRetention(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "manager"
        self.root.mkdir(mode=0o700)
        self.engine = SupervisorSession(self.root)
        self.thread = str(uuid.uuid4())

    def state(self, requests):
        return {"schema_version": 1, "thread_id": self.thread, "requests": requests}

    def artifact(self):
        name = "turn-" + uuid.uuid4().hex
        directory = self.root / name
        directory.mkdir(mode=0o700)
        (directory / "context.json").write_text("synthetic evidence")
        (directory / "response.json").write_text("synthetic response")
        return name

    def test_caps_artifacts_and_recent_request_receipts_without_resetting_thread(self):
        requests = {f"request-{i}": {"status": "completed"} for i in range(MAX_REQUESTS + 3)}
        directories = []
        for i in range(MAX_TURN_DIRECTORIES + 3):
            name = self.artifact()
            directories.append(name)
            requests[f"request-{MAX_REQUESTS - MAX_TURN_DIRECTORIES + i}"]["directory"] = name
        unrelated = self.root / ("turn-" + uuid.uuid4().hex)
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("not recorded by supervisor")
        state = self.state(requests)
        self.engine._save(state)
        restored = json.loads((self.root / "session.json").read_text())
        self.assertEqual(restored["thread_id"], self.thread)
        self.assertEqual(len(restored["requests"]), MAX_REQUESTS)
        self.assertEqual(next(iter(restored["requests"])), "request-3")
        self.assertEqual(len([r for r in restored["requests"].values() if "directory" in r]), MAX_TURN_DIRECTORIES)
        for name in directories[:3]:
            self.assertFalse((self.root / name).exists())
        for name in directories[3:]:
            self.assertTrue((self.root / name / "response.json").exists())
        self.assertEqual((unrelated / "keep.txt").read_text(), "not recorded by supervisor")
        self.engine._save(restored)
        self.assertEqual(json.loads((self.root / "session.json").read_text()), restored)

    def test_pruning_does_not_follow_directory_symlinks(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        marker = outside / "response.json"
        marker.write_text("must survive")
        name = "turn-" + uuid.uuid4().hex
        (self.root / name).symlink_to(outside, target_is_directory=True)
        state = self.state({"old": {"status": "failed", "directory": name},
                            "new": {"status": "started", "directory": self.artifact()}})
        with patch("supervisor_session.MAX_TURN_DIRECTORIES", 1), self.assertRaises(SupervisorError) as error:
            self.engine._save(state)
        self.assertEqual(error.exception.code, "unsafe_state")
        self.assertTrue((self.root / name).is_symlink())
        self.assertEqual(marker.read_text(), "must survive")

    def test_unknown_files_subdirectories_and_artifact_symlinks_prevent_cleanup(self):
        outside = Path(self.temporary.name) / "outside-file"
        outside.write_text("must survive")
        for kind in ("unknown_file", "subdirectory", "symlink"):
            with self.subTest(kind=kind):
                name = self.artifact()
                directory = self.root / name
                if kind == "unknown_file":
                    (directory / "user-data.txt").write_text("must survive")
                elif kind == "subdirectory":
                    (directory / "subdirectory").mkdir()
                else:
                    (directory / "plan.json").symlink_to(outside)
                state = self.state({"old": {"status": "failed", "directory": name},
                                    "new": {"status": "started", "directory": self.artifact()}})
                with patch("supervisor_session.MAX_TURN_DIRECTORIES", 1), self.assertRaises(SupervisorError):
                    self.engine._save(state)
                self.assertEqual((directory / "context.json").read_text(), "synthetic evidence")
                self.assertEqual(outside.read_text(), "must survive")

    def test_invalid_recorded_paths_never_become_cleanup_targets(self):
        for name in ("../outside", "/tmp/elsewhere", "turn-not-a-uuid", "turn-" + "0" * 32 + "/child"):
            with self.subTest(name=name), self.assertRaises(SupervisorError) as error:
                self.engine._save(self.state({"bad": {"status": "failed", "directory": name}}))
            self.assertEqual(error.exception.code, "unsafe_state")

    def test_missing_old_artifact_is_reconciled_without_losing_duplicate_receipt(self):
        name = "turn-" + uuid.uuid4().hex
        state = self.state({"old": {"status": "canceled", "directory": name},
                            "new": {"status": "started", "directory": self.artifact()}})
        with patch("supervisor_session.MAX_TURN_DIRECTORIES", 1):
            self.engine._save(state)
        self.assertEqual(state["requests"]["old"], {"status": "canceled"})
        self.assertEqual(state["thread_id"], self.thread)


class RecentDuplicateDefense(unittest.IsolatedAsyncioTestCase):
    async def test_recent_pruned_artifact_still_blocks_duplicate_before_provider_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "manager"
            root.mkdir(mode=0o700)
            engine = SupervisorSession(root)
            state = {"schema_version": 1, "thread_id": str(uuid.uuid4()), "requests": {
                "one": {"status": "completed"}, "newer": {"status": "canceled"}}}
            engine._save(state)
            with patch("supervisor_session.asyncio.create_subprocess_exec") as spawn:
                with self.assertRaises(SupervisorError) as error:
                    await engine.turn({"request_id": "one", "user_text": "Summarize the work.",
                        "route": "manager_question", "source": "utterance", "observations": [],
                        "candidates": [], "history": [], "message_sources": [],
                        "allowed_operations": ["answer"], "allowed_target_ids": []})
            self.assertEqual(error.exception.code, "duplicate_request")
            spawn.assert_not_called()
            self.assertEqual(engine.thread_id, state["thread_id"])


if __name__ == "__main__":
    unittest.main()
