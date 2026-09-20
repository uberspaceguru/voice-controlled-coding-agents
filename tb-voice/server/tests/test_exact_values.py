import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.gradium.tts import GradiumTTSService

import manager
from exact_speech import ExactSpeakFrame
from exact_values import ExactValue, exact_request, recorded_value
from manager import Manager
from spoken import spoken
from tts import SpokenGradiumTTSService, _literal

SID = "01a0bbd6-1d45-7ce3-9911-c19c484bd757"
PATH = "/Users/demo/Hackathon AGI House/voice-controlled-coding-agents/.claude/worktrees/demo"


class Values(unittest.TestCase):
    def test_request_corpus(self):
        cases = json.loads((Path(__file__).parents[1] / "evals/exact_requests.json").read_text())
        for c in cases:
            with self.subTest(text=c["text"]):
                self.assertEqual(exact_request(c["text"]), c["exact"])

    def test_facts_not_questions_or_proposals(self):
        target = {"sessionId": SID, "cwd": PATH}
        self.assertEqual(recorded_value("directory", target, {}).value, PATH)
        self.assertEqual(recorded_value("identifier", target, {}).value, SID)
        self.assertIsNone(
            recorded_value("branch", target, {"sessionId": SID, "proposal": "Branch: invented"})
        )
        self.assertIsNone(recorded_value("directory", target, {"sessionId": "different"}))

    def test_reported_values_and_missing_facts(self):
        for kind, message, expected in [
            ("branch", "Branch: `feature/exact-values`", "feature/exact-values"),
            ("command", "Command: `git status --short`", "git status --short"),
        ]:
            self.assertEqual(
                recorded_value(
                    kind, {"sessionId": SID}, {"sessionId": SID, "lastAssistantMessage": message}
                ).value,
                expected,
            )
        for text in [
            "Command: run some tests",
            "Command: `one`\nCommand: `two`",
            "Command: `echo ok` then run it",
            "Command: `export API_KEY=secret`",
        ]:
            self.assertIsNone(
                recorded_value(
                    "command", {"sessionId": SID}, {"sessionId": SID, "lastAssistantMessage": text}
                )
            )

    def test_literal_validation_and_ordinary_sanitizer(self):
        for value in ["", "x" * 601, "line\nbreak", "gsk_example", "API_KEY=abc"]:
            with self.assertRaises(ValueError):
                ExactValue("command", value)
        self.assertNotIn("/Users/demo", spoken(PATH))
        self.assertNotIn(SID, spoken(SID))
        with self.assertRaises(ValueError):
            ExactSpeakFrame(text="invented", value=ExactValue("directory", PATH))

    def test_truncated_branch_is_not_reported_as_complete(self):
        text = "Branch: " + "a" * 592
        self.assertIsNone(
            recorded_value(
                "branch", {"sessionId": SID}, {"sessionId": SID, "lastAssistantMessage": text}
            )
        )


class Routing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.memory_events = patch("memory_manager.emit", AsyncMock())
        self.memory_events.start()
        self.addCleanup(self.memory_events.stop)
        self.events = patch("dialogue_manager.emit", AsyncMock())
        self.events.start()
        self.addCleanup(self.events.stop)

    def make_manager(self, intent="custom", p=1):
        m = object.__new__(Manager)
        m._init_dialogue()
        m.stage = {"sessionId": SID, "name": "demo"}
        m._recent = []
        m.addressed = 0
        m._jev = AsyncMock()
        m._jev.last = {}
        m._jev.turn.return_value = (p, {"choice": intent, "confidence": 1})
        def choice(value): return {"choice":value, "probabilities":{value:1.0}}
        m._jev.ask.return_value = {
            "addressed":{"noul":p}, "execute":{"noul":1},
            "act":choice("direct" if intent in {"send_message", "custom"} else "inform"),
            "route":choice(intent), "target":choice("stage"),
            "source":choice("utterance"), "response":choice(intent if intent.startswith("exact_") else "summary"),
        }
        m._dialogue_dispatch = AsyncMock()
        m._targets = AsyncMock(return_value=[{"sessionId": SID, "cwd": PATH}])
        m._brief = AsyncMock(return_value={"sessionId": SID})
        m._say = AsyncMock()
        m._earcon = AsyncMock()
        m._llm = AsyncMock()
        m._do_send_message = AsyncMock()
        return m

    async def test_directory_regression_cannot_dispatch_even_if_model_says_send(self):
        m = self.make_manager("send_message")
        with patch.object(manager, "emit", AsyncMock()), patch.object(manager, "note"):
            await m._turn("Give me the full directory path.", None, None)
        m._do_send_message.assert_not_awaited()
        m._llm.assert_not_awaited()
        m._jev.is_action.assert_not_awaited()
        self.assertEqual(m._say.call_args.args[0], PATH)
        self.assertEqual(m._say.call_args.kwargs["exact"].value, PATH)

    async def test_unaddressed_exact_request_stays_silent(self):
        m = self.make_manager("exact_directory", p=0.1)
        with patch.object(manager, "emit", AsyncMock()), patch.object(manager, "note"):
            await m._turn("Give me the full directory path.", None, None)
        m._say.assert_not_awaited()
        m._dialogue_dispatch.assert_not_awaited()

    async def test_question_containing_send_not_overridden_to_action(self):
        m = self.make_manager("exact_command")
        with patch.object(manager, "emit", AsyncMock()), patch.object(manager, "note"):
            await m._turn("What command did you send to this agent?", None, None)
        m._do_send_message.assert_not_awaited()
        self.assertIsNone(m._say.call_args.kwargs.get("exact"))

    async def test_no_stage_and_dead_session_do_not_fabricate(self):
        m = self.make_manager()
        m.stage = None
        await m._exact_value("directory")
        m._targets.assert_not_awaited()
        m = self.make_manager()
        m._targets.return_value = []
        await m._exact_value("directory")
        self.assertIsNone(m._say.call_args.kwargs.get("exact"))

    async def test_known_values_are_literal_and_read_only(self):
        for kind, text, expected in [
            ("branch", "Branch: `feature/exact-values`", "feature/exact-values"),
            ("command", "Command: `git status --short`", "git status --short"),
            ("identifier", "", SID),
        ]:
            m = self.make_manager()
            m._brief.return_value = {"sessionId": SID, "lastAssistantMessage": text}
            await m._exact_value(kind)
            self.assertEqual(m._say.call_args.args[0], expected)
            self.assertEqual(m._say.call_args.kwargs["exact"].value, expected)
            m._llm.assert_not_awaited()
            m._do_send_message.assert_not_awaited()

    async def test_ordinary_model_answer_still_sanitized(self):
        m = self.make_manager()
        m._brain = AsyncMock()
        m._brain.answer.return_value = PATH
        m._app_speaks = AsyncMock()
        with patch.object(manager, "emit", AsyncMock()), patch.object(manager, "note"):
            await m._answer_about_stage("Explain what changed", {"sessionId": SID})
        self.assertEqual(m._app_speaks.call_args.args[1], spoken(PATH))
        m._say.assert_not_awaited()

    async def test_explicit_send_still_dispatches(self):
        m = self.make_manager("custom")
        with patch.object(manager, "emit", AsyncMock()), patch.object(manager, "note"):
            await m._turn("Send a message to this agent: run the tests", None, None)
        m._dialogue_dispatch.assert_awaited_once()
        m._say.assert_not_awaited()

    async def test_tts_literal_scope_survives_await_and_resets_after_failure(self):
        service = object.__new__(SpokenGradiumTTSService)
        heard = []

        async def synth(self, text, context):
            heard.append(text)
            if False:
                yield None

        async def process(self, frame, direction):
            await asyncio.sleep(0)
            async for _ in self.run_tts(frame.text, "ctx"):
                pass

        long_path = "/Users/demo/" + "long path " * 35
        with (
            patch.object(GradiumTTSService, "process_frame", process),
            patch.object(GradiumTTSService, "run_tts", synth),
            patch.object(manager, "note"),
        ):
            await asyncio.gather(
                service.process_frame(
                    ExactSpeakFrame(text=long_path, value=ExactValue("directory", long_path)),
                    FrameDirection.DOWNSTREAM,
                ),
                service.process_frame(TTSSpeakFrame(PATH), FrameDirection.DOWNSTREAM),
            )
        self.assertEqual(heard, [long_path, spoken(PATH)])
        self.assertIsNone(_literal.get())
        with patch.object(GradiumTTSService, "process_frame", AsyncMock(side_effect=RuntimeError)):
            with self.assertRaises(RuntimeError):
                await service.process_frame(
                    ExactSpeakFrame(text=PATH, value=ExactValue("directory", PATH)),
                    FrameDirection.DOWNSTREAM,
                )
        self.assertIsNone(_literal.get())


if __name__ == "__main__":
    unittest.main()
