"""Existing-terminal navigation through real dialogue handlers, with CLI mocked.

These establish selection and side effects, not a visible terminal or live audio.
"""

import asyncio
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_memory_manager import SpeechEvidence, make_manager
from test_supervisor_manager import judgment

from dialogue import Record
from evals.dialogue_fixture import (
    begin_turn,
    choice,
    contract_errors,
    mocked_judgment,
    policy_observation,
    seed_dialogue,
)


class FocusEvaluationContract(unittest.TestCase):
    def test_synthetic_development_cases_have_executable_policy_contracts(self):
        corpus = json.loads((Path(__file__).parents[1] / "evals/focus_supervisor_development.json").read_text())
        for group in corpus["conversations"]:
            for turn in group["turns"]:
                with self.subTest(case=turn["id"]):
                    dialogue = seed_dialogue(turn, corpus["fixtures"])
                    epoch = begin_turn(dialogue, turn)
                    observed = policy_observation(dialogue, turn, mocked_judgment(turn), epoch)
                    self.assertEqual(contract_errors(turn, observed), [])
                    if turn["expect"].get("focus_target"):
                        observed["target"] = "alpha"
                        self.assertIn("focus_target", contract_errors(turn, observed))


class FocusDialogue(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for module in ("manager", "dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.run = self.patches.enter_context(patch("tools._run", AsyncMock(return_value=(0, ""))))
        self.native_run = self.patches.enter_context(patch("manager._run", AsyncMock()))

    def fixture(self, **kwargs):
        m = make_manager()
        m._supervisor = AsyncMock()
        m._supervisor_journal = None
        m._jev.ask.return_value = judgment(route="focus_agent", target="beta", response="receipt", **kwargs)
        return m, SpeechEvidence(m)

    def assert_navigation_only(self, m):
        self.assertIsNone(m.dialogue.last_action)
        m._brain.answer.assert_not_awaited()
        m._brain.compose_message.assert_not_awaited()
        m._supervisor.turn.assert_not_awaited()
        self.native_run.assert_not_awaited()
        for call in self.run.await_args_list:
            self.assertEqual(call.args[1], "focus")

    async def test_named_navigation_uses_focus_only_for_inform_direct_and_control(self):
        for act in ("inform", "direct", "control"):
            with self.subTest(act=act):
                self.run.reset_mock()
                m, speech = self.fixture(act=act)
                await m._turn("Bring up Beta's existing terminal.", None, None)
                self.run.assert_awaited_once()
                self.assertEqual(self.run.call_args.args[1:], ("focus", "beta"))
                self.assertEqual(speech.frames[-1].text, "Terminal opened.")
                self.assert_navigation_only(m)

    async def test_work_instruction_still_sends_original_text_instead_of_focusing(self):
        m, speech = self.fixture()
        text = "Tell Beta to review the terminal rendering tests."
        m._jev.ask.return_value = judgment(act="direct", route="send_message", target="beta",
                                         response="receipt", execute=0.99)
        await m._turn(text, None, None)
        self.assertEqual(self.run.call_args.args[1:], ("send", "beta", text))
        self.assertEqual(m.dialogue.last_action.status, "sent")
        self.assertEqual(speech.frames[-1].text, "Sent.")
        m._supervisor.turn.assert_not_awaited()

    async def test_ambiguous_navigation_has_one_question_then_bounded_receipt_then_silence(self):
        m, speech = self.fixture()
        m._jev.ask.return_value["target"] = choice("ambiguous")
        for _ in range(3):
            await m._turn("Open that agent's terminal.", None, None)
        self.assertEqual(len(speech.frames), 2)
        self.assertIn("Which agent", speech.frames[0].text)
        self.assertIn("couldn't resolve", speech.frames[1].text)
        self.run.assert_not_awaited()
        self.assert_navigation_only(m)
        m._jev.ask.return_value["target"] = choice("beta")
        await m._turn("Open Beta's terminal.", None, None)
        self.assertEqual(self.run.call_args.args[1:], ("focus", "beta"))

    async def test_uncertain_target_does_not_fall_back_to_stage(self):
        m, speech = self.fixture()
        m._jev.ask.return_value["target"] = choice("beta", 0.40)
        await m._turn("Show Beta's terminal.", None, None)
        self.run.assert_not_awaited()
        self.assertIn("Which agent", speech.frames[-1].text)
        self.assert_navigation_only(m)

    async def test_quoted_ack_and_side_speech_never_open_terminal(self):
        for act, text in (("think", 'The guide says "open Beta\'s terminal".'),
                          ("think", "Morgan, show Beta's terminal on your laptop."),
                          ("ack", "mm-hmm")):
            with self.subTest(act=act, text=text):
                m, speech = self.fixture(act=act)
                await m._turn(text, None, None)
                self.assertEqual(speech.frames, [])
                self.run.assert_not_awaited()
                self.assert_navigation_only(m)

    async def test_weak_addressedness_or_route_cannot_open_terminal(self):
        for question in ("addressed", "route"):
            with self.subTest(question=question):
                m, _ = self.fixture()
                m._jev.ask.return_value[question] = ({"noul": 0.60} if question == "addressed"
                                                   else choice("focus_agent", 0.60))
                await m._turn("Show Beta's terminal.", None, None)
                self.run.assert_not_awaited()
                self.assert_navigation_only(m)

    async def test_absent_candidate_and_expired_previous_reference_are_not_focused(self):
        for absent in (True, False):
            with self.subTest(absent=absent):
                m, speech = self.fixture()
                if absent:
                    m._targets.return_value = [m.stage]
                else:
                    m.dialogue.last_action = Record("Review checks.", "beta", m.dialogue.clock() - 91, "sent")
                    m._jev.ask.return_value["target"] = choice("previous")
                await m._turn("Open that terminal.", None, None)
                self.run.assert_not_awaited()
                self.assertIn("Which agent", speech.frames[-1].text)
                m._supervisor.turn.assert_not_awaited()

    async def test_stage_switch_during_judgment_prevents_focus(self):
        m, speech = self.fixture()
        answer = m._jev.ask.return_value
        async def ask(*args):
            m.stage = m._targets.return_value[1]
            return answer
        m._jev.ask.side_effect = ask
        with self.assertRaises(asyncio.CancelledError):
            await m._turn("Show Beta's terminal.", None, None)
        self.run.assert_not_awaited()
        self.assertEqual(speech.frames, [])

    async def test_cancel_supersedes_a_late_navigation_judgment(self):
        m, _ = self.fixture()
        started, release = asyncio.Event(), asyncio.Event()
        focus_answer = m._jev.ask.return_value
        async def ask(*args):
            started.set()
            await release.wait()
            return focus_answer
        m._jev.ask.side_effect = ask
        old = asyncio.create_task(m._turn("Open Beta's terminal.", None, None))
        await started.wait()
        m._jev.ask.side_effect = None
        m._jev.ask.return_value = judgment(act="cancel", route="none", target="none", response="receipt")
        await m._turn("Never mind.", None, None)
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        self.run.assert_not_awaited()
        self.assert_navigation_only(m)

    async def test_duplicate_final_transcript_does_not_repeat_focus(self):
        m, _ = self.fixture()
        self.assertTrue(m._schedule_dialogue("Open Beta's terminal.", None, None, "synthetic-focus:1"))
        await m._handler
        self.assertFalse(m._schedule_dialogue("Open Beta's terminal.", None, None, "synthetic-focus:1"))
        self.run.assert_awaited_once()
        self.assert_navigation_only(m)

    async def test_navigation_failure_reports_failure_without_send_restart_or_retry(self):
        for code in (2, 5, 124):
            with self.subTest(code=code):
                self.run.reset_mock()
                self.run.return_value = (code, "synthetic failure")
                m, speech = self.fixture()
                await m._turn("Open Beta's terminal.", None, None)
                self.run.assert_awaited_once()
                self.assertIn("couldn't open", speech.frames[-1].text)
                self.assertIn("not restarted", speech.frames[-1].text)
                self.assert_navigation_only(m)

    async def test_navigation_does_not_authorize_or_send_an_existing_pending_payload(self):
        m, _ = self.fixture()
        pending = m.dialogue.prepare("Run the checks.", "alpha")
        m.dialogue.mark_offered(pending.pending_id)
        await m._turn("Show Beta's terminal.", None, None)
        self.assertEqual(self.run.call_args.args[1:], ("focus", "beta"))
        self.assert_navigation_only(m)
        self.assertEqual(m.dialogue.pending.text, "Run the checks.")
        self.assertEqual(m.dialogue.pending.target, "alpha")
