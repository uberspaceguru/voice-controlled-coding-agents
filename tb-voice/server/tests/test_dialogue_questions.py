"""Question/state boundary checks. Semantic accuracy is measured live separately."""

import copy
import json
import unittest

from dialogue_questions import build_questions, judgment_state


class DialogueQuestionContractTests(unittest.TestCase):
    def test_live_targets_are_candidate_data_not_a_static_name_list(self):
        targets = [
            {"sessionId": "session-one", "name": "Saffron", "goal": "Maintain search"},
            {"sessionId": "session-two", "name": "Nimbus", "goal": "Maintain indexing"},
        ]
        questions = build_questions({"custom": "Read a record", "none": "No route"}, targets)
        candidates = questions["target"]["criteria"]
        self.assertEqual(set(candidates), {
            "session-one", "session-two", "stage", "previous", "ambiguous", "none",
        })
        self.assertIn("Saffron", candidates["session-one"])
        self.assertIn("Nimbus", candidates["session-two"])
        # A new candidate name can be grounded without changing any policy or
        # adding a named-person exception to the question definitions.
        targets[0]["name"] = "Cobalt"
        revised = build_questions({"none": "No route"}, targets)
        self.assertIn("Cobalt", revised["target"]["criteria"]["session-one"])
        self.assertNotIn("Cobalt", candidates["session-one"])

    def test_batch_contract_keeps_orthogonal_judgments_and_existing_routes(self):
        routes = {"custom": "Read a record", "send_message": "Send work", "none": "No route"}
        original = copy.deepcopy(routes)
        questions = build_questions(routes, [])
        self.assertEqual(routes, original)
        self.assertEqual(set(questions), {
            "addressed", "act", "route", "target", "source", "response", "execute",
        })
        self.assertEqual(questions["addressed"]["type"], "noul")
        self.assertEqual(questions["execute"]["type"], "noul")
        self.assertTrue(set(original) <= set(questions["route"]["criteria"]))
        self.assertIn("unknown", questions["source"]["criteria"])
        self.assertIn("ambiguous", questions["target"]["criteria"])
        self.assertIn("last_action", questions["source"]["criteria"])

    def test_snapshot_excludes_runtime_settings_and_preserves_bound_records(self):
        snapshot = {
            "stage": "session-one",
            "targets": [{"sessionId": "session-one", "name": "Saffron", "api_key": "PRIVATE"}],
            "pending": {"id": 3, "text": "Run the index tests", "target": "session-one", "age": 4,
                        "offered": True, "status": "confirm", "credentials": "PRIVATE"},
            "last_action": {"text": "Read the report", "target": "session-one", "status": "sent", "age": 5},
            "secrets": "PRIVATE", "runtime_command": "PRIVATE", "recent": [],
        }
        state = judgment_state("What did you send?", snapshot)
        serialized = json.dumps(state)
        self.assertNotIn("PRIVATE", serialized)
        self.assertEqual(state["conversation"]["pending"]["offered"], True)
        self.assertEqual(state["conversation"]["last_action"]["status"], "sent")
        state["conversation"]["pending"]["text"] = "Changed after classification"
        state["conversation"]["targets"][0]["name"] = "Changed name"
        self.assertEqual(snapshot["pending"]["text"], "Run the index tests")
        self.assertEqual(snapshot["targets"][0]["name"], "Saffron")

    def test_complete_text_and_information_context_reach_every_judgment(self):
        # Prefix truncation could hide the final prohibition while execution
        # later receives the full request. The state must retain the same text.
        text = "Discuss the recorded checks. " * 180 + "Do not run any of them."
        snapshot = {
            "stage": "session-one", "targets": [], "pending": None,
            "last_command": {"text": text, "target": "session-one", "age": 5},
            "last_information": "exact_branch",
            "recent": [{"text": "Read the branch", "act": "inform", "outcome": "exact_branch"}],
        }
        state = judgment_state(text, snapshot)
        self.assertEqual(state["text"], text)
        self.assertEqual(state["conversation"]["last_command"]["text"], text)
        self.assertEqual(state["conversation"]["last_information"], "exact_branch")
        self.assertEqual(state["conversation"]["recent"][0]["outcome"], "exact_branch")


if __name__ == "__main__":
    unittest.main()
