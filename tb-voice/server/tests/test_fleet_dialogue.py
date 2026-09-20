"""Fleet questions must not enter an agent-selection clarification loop.

All target records and judgments are synthetic; actual policy/handler/speech
boundaries run with in-process output evidence. No fleet writes or providers run.
"""

import unittest
from contextlib import ExitStack
from types import MethodType
from unittest.mock import AsyncMock, patch

from test_memory_manager import SpeechEvidence, make_manager

from manager import FleetReadError, Manager


def choice(value):
    return {"choice": value, "probabilities": {value: 0.99}, "confidence": 0.99}


def judgment(route, target="none", response="summary"):
    return {
        "addressed": {"noul": 0.99}, "execute": {"noul": 0.01},
        "act": choice("inform"), "target": choice(target), "source": choice("utterance"),
        "response": choice(response), "route": choice(route),
    }


def fleet(count):
    return [
        {"sessionId": f"session-{index}", "name": f"Worker {index:02d}",
         "goal": f"Review synthetic component {index}", "project": f"demo-{index}",
         "cwd": f"/demo/component-{index}"}
        for index in range(count)
    ]


def fixture(count=7, staged=False):
    m = make_manager()
    targets = fleet(count)
    m.stage = targets[0].copy() if staged and targets else None
    m.dialogue.sync(m.stage and m.stage["sessionId"], targets)
    m._targets = AsyncMock(return_value=targets)
    m._brain.plain.return_value = "I can answer manager-level questions from the current records."
    speech = SpeechEvidence(m)
    return m, speech, targets


class FleetDialogue(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        for module in ("manager", "dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.run = self.patches.enter_context(patch("tools._run", AsyncMock(return_value=(0, ""))))
        self.native_run = self.patches.enter_context(patch("manager._run", AsyncMock(return_value=(0, ""))))

    def tearDown(self):
        self.run.assert_not_awaited()
        self.native_run.assert_not_awaited()
        self.patches.close()

    @staticmethod
    def spoken(speech):
        return " ".join(frame.text for frame in speech.frames)

    def assert_read_only(self, m):
        self.assertIsNone(m.dialogue.pending)
        self.assertIsNone(m.dialogue.last_action)
        m._brain.compose_message.assert_not_awaited()

    async def test_fleet_count_uses_all_live_targets_without_stage_or_answer_model(self):
        m, speech, _ = fixture(count=37)
        m._jev.ask.return_value = judgment("fleet_inventory")
        await m._turn("How many coding sessions are connected?", None, None)
        text = self.spoken(speech)
        self.assertRegex(text, r"\b37\b")
        self.assertIn("live agent", text.lower())
        self.assertNotIn("which agent", text.lower())
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        m._brief.assert_not_awaited()
        self.assert_read_only(m)

    async def test_count_route_separates_activity_enrollment_and_live_count_without_names(self):
        m, speech, targets = fixture(count=4)
        targets[0].update(status="busy", enrolled=True, waiting=False)
        targets[1].update(status="idle", enrolled=False, waiting=True)
        targets[2].update(status="waiting", enrolled=True, waiting=False)
        targets[3].update(enrolled=False, waiting=True)
        m._fleet_inventory = AsyncMock(wraps=m._fleet_inventory)
        m._jev.ask.return_value = judgment("fleet_count")
        await m._turn("How many agents are active right now?", None, None)
        m._fleet_inventory.assert_awaited_once_with(include_names=False)
        text = self.spoken(speech).lower()
        for fragment in ("4 live agents", "1 busy", "1 idle", "1 waiting", "1 unknown", "2 enrolled"):
            self.assertIn(fragment, text)
        self.assertNotIn("2 waiting", text)
        self.assertNotIn("4 busy", text)
        self.assertNotIn("4 working", text)
        for target in targets:
            self.assertNotIn(target["name"].lower(), text)
        self.assertEqual(len(speech.frames), 1)
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_read_only(m)

    async def test_manager_scope_keeps_waiting_for_reply_independent_of_activity(self):
        m, _, targets = fixture(count=4)
        targets[0].update(status="busy", enrolled=True, waiting=False)
        targets[1].update(status="idle", enrolled=False, waiting=True)
        targets[2].update(status="waiting", enrolled=True, waiting=False)
        targets[3].update(enrolled=False, waiting=True)
        m._jev.ask.return_value = judgment("custom")
        await m._turn("What can you tell me about the fleet's current state?", None, None)
        scope = m._brain.plain.call_args.kwargs["scope"]
        self.assertEqual(scope["live_agent_count"], 4)
        states = scope["agent_states"]
        self.assertEqual([row["activity"] for row in states], ["busy", "idle", "waiting", "unknown"])
        self.assertEqual([row["waiting_for_reply"] for row in states], [False, True, False, True])
        self.assertEqual(sum(row["enrolled"] for row in states), 2)
        self.assertIn("not necessarily busy", scope["semantics"])
        self.assert_read_only(m)

    async def test_fleet_list_reads_concrete_names_without_selecting_an_agent(self):
        m, speech, targets = fixture(count=7)
        m._jev.ask.return_value = judgment("fleet_inventory", response="detail")
        await m._turn("Name all the live coding agents.", None, None)
        text = self.spoken(speech)
        self.assertRegex(text, r"\b7\b")
        for target in targets:
            self.assertIn(target["name"], text)
        self.assertNotIn("which agent", text.lower())
        self.assertIsNone(m.stage)
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_read_only(m)

    async def test_empty_inventory_reports_zero_instead_of_asking_for_a_target(self):
        m, speech, _ = fixture(count=0)
        m._jev.ask.return_value = judgment("fleet_inventory")
        await m._turn("Are any coding agents available?", None, None)
        self.assertRegex(self.spoken(speech), r"\b0\b")
        self.assertNotIn("which agent", self.spoken(speech).lower())
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_read_only(m)

    async def test_fleet_scope_is_not_narrowed_by_an_existing_stage_selection(self):
        m, speech, _ = fixture(count=11, staged=True)
        original_stage = m.stage.copy()
        m._jev.ask.return_value = judgment("fleet_inventory", target="stage")
        await m._turn("Give me the current fleet count.", None, None)
        self.assertRegex(self.spoken(speech), r"\b11\b")
        self.assertEqual(m.stage, original_stage)
        m._brief.assert_not_awaited()
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_read_only(m)

    async def test_general_no_target_custom_and_none_routes_do_not_ask_for_an_agent(self):
        for route in ("custom", "none"):
            with self.subTest(route=route):
                m, speech, targets = fixture(count=7)
                m._jev.ask.return_value = judgment(route)
                await m._turn("Can you describe what information you currently have?", None, None)
                self.assertNotIn("which agent", self.spoken(speech).lower())
                self.assertIn("manager-level questions", self.spoken(speech))
                m._brain.plain.assert_awaited_once()
                m._brain.answer.assert_not_awaited()
                supplied = m._brain.plain.call_args.kwargs["scope"]
                self.assertEqual(supplied["live_agent_count"], len(targets))
                for target in targets:
                    self.assertIn(target["name"], supplied["live_agents"])
                self.assertTrue(supplied["capabilities"])
                self.assertFalse(supplied["stage_selected"])
                self.assert_read_only(m)

    async def test_manager_status_does_not_need_stage_or_text_generation(self):
        m, speech, _ = fixture(count=7)
        m._jev.ask.return_value = judgment("manager_status", response="receipt")
        await m._turn("Can you hear my requests?", None, None)
        self.assertTrue(speech.frames)
        self.assertNotIn("which agent", self.spoken(speech).lower())
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        self.assert_read_only(m)

    async def test_genuine_missing_agent_gets_one_question_then_receipt_then_silence(self):
        for route, response, text in (
            ("exact_branch", "exact_branch", "Which branch is that agent using?"),
            ("rung_goal", "summary", "What is that agent working on?"),
        ):
            with self.subTest(route=route):
                m, speech, _ = fixture(count=7)
                m._jev.ask.return_value = judgment(route, target="ambiguous", response=response)
                await m._turn(text, None, None)
                self.assertEqual(len(speech.frames), 1)
                self.assertIn("?", speech.frames[0].text)
                await m._turn(text, None, None)
                self.assertEqual(len(speech.frames), 2)
                self.assertNotIn("?", speech.frames[1].text)
                await m._turn(text, None, None)
                self.assertEqual(len(speech.frames), 2)
                self.assertEqual(sum("which agent" in frame.text.lower() for frame in speech.frames), 1)
                m._brain.answer.assert_not_awaited()
                m._brain.plain.assert_not_awaited()
                self.assert_read_only(m)

    async def test_targets_read_distinguishes_valid_empty_from_failure_or_malformed_data(self):
        m, _, _ = fixture()
        invalid = (
            (2, "[]"), (124, "timed out"), (0, "not json"), (0, "{}"),
            (0, '["not a target"]'), (0, '[{"name":"Missing identity"}]'),
            (0, '[{"sessionId":""}]'),
        )
        for result in invalid:
            with self.subTest(result=result), patch("manager._run", AsyncMock(return_value=result)) as read:
                with self.assertRaises(FleetReadError):
                    await Manager._targets(m)
                read.assert_awaited_once()
                self.assertEqual(read.call_args.args[1:], ("targets", "--json"))
        with patch("manager._run", AsyncMock(return_value=(0, "[]"))) as read:
            self.assertEqual(await Manager._targets(m), [])
            read.assert_awaited_once()
            self.assertEqual(read.call_args.args[1:], ("targets", "--json"))

    async def test_handle_turn_reports_failed_inventory_read_as_unavailable_not_zero(self):
        for result in ((5, "synthetic read failure"), (0, "{malformed")):
            with self.subTest(result=result):
                m, speech, _ = fixture()
                m._targets = MethodType(Manager._targets, m)
                m._jev.ask.return_value = judgment("fleet_inventory")
                with patch("manager._run", AsyncMock(return_value=result)) as read:
                    await m._handle_turn("How many coding agents are live?", None, None)
                    read.assert_awaited_once()
                    self.assertEqual(read.call_args.args[1:], ("targets", "--json"))
                text = self.spoken(speech).lower()
                self.assertRegex(text, r"can't read|unavailable")
                self.assertNotRegex(text, r"\b0\b|\bzero\b")
                self.assertNotIn("which agent", text)
                m._jev.ask.assert_not_awaited()
                m._brain.answer.assert_not_awaited()
                m._brain.plain.assert_not_awaited()
                self.assert_read_only(m)


if __name__ == "__main__":
    unittest.main()
