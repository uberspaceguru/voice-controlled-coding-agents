import json
import unittest

from supervisor_manager import bounded_context
from supervisor_session import prepare_context


class ContextBounds(unittest.TestCase):
    def test_long_history_and_briefs_fit_without_losing_current_request_or_identities(self):
        candidates = [{"session_id": str(i), "name": f"Agent {i}"} for i in range(100)]
        observations = [{"id": "fleet", "kind": "fleet", "session_id": None,
                         "observed_at": 100, "data": {"verified_agents": 100}}]
        observations += [{"id": f"brief-{i}", "kind": "stored_brief", "session_id": str(i),
                          "observed_at": 100, "data": {"recap": "é" * 4800}} for i in range(32)]
        context = {"request_id": "r", "user_text": "What changed for Agent 0?", "route": "supervise",
                   "source": "utterance", "candidates": candidates, "observations": observations,
                   "message_sources": [], "allowed_operations": ["answer", "clarify"],
                   "allowed_target_ids": [], "work_memory": {}, "stage": "0",
                   "history": [{"text": "h" * 4000} for _ in range(40)],
                   "inferred_notes": [{"summary": "n" * 1200} for _ in range(128)]}
        result = bounded_context(context)
        prepare_context(result)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 240_000)
        self.assertEqual(len(result["candidates"]), 100)
        self.assertEqual(result["user_text"], "What changed for Agent 0?")
        self.assertIn("brief-0", [row["id"] for row in result["observations"]])
        omitted = result["observations"][0]["data"]["context_omissions"]
        self.assertGreater(omitted["inferred_notes"], 0)
        self.assertGreater(omitted["history"], 0)
        self.assertGreater(omitted["source_briefs"], 0)

    def test_small_context_keeps_every_source(self):
        context = {"observations": [{"data": {}}], "inferred_notes": [{"summary": "keep"}],
                   "history": [{"text": "keep"}], "candidates": [{"session_id": "a"}]}
        result = bounded_context(context)
        self.assertEqual(result["inferred_notes"], [{"summary": "keep"}])
        self.assertEqual(result["history"], [{"text": "keep"}])
        self.assertEqual(result["observations"][0]["data"]["context_omissions"], {})
