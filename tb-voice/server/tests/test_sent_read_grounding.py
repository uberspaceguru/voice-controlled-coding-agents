"""A sole confirmed sent record can ground a read, never a new dispatch."""

import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from test_memory_manager import SpeechEvidence, make_manager

from dialogue import Dialogue, Record
from evals.dialogue_fixture import Clock, choice, semantic_answers


class SentReadGrounding(unittest.TestCase):
    def fixture(self):
        clock = Clock()
        d = Dialogue(clock)
        d.sync("alpha", [{"sessionId": "alpha"}, {"sessionId": "beta"}])
        d.last_action = Record("python -m pytest tests/test_browser.py -q", "beta", clock() - 4, "sent")
        a = semantic_answers(act="inform", target="none", source="last_action",
                             response="exact_command", route="exact_command", execute=0.05)
        a["target"] = {"choice": "none", "confidence": 0.38,
                       "probabilities": {"none": 0.49, "beta": 0.37, "previous": 0.14,
                                         "alpha": 0, "stage": 0, "ambiguous": 0}}
        a["source"] = {"choice": "last_action", "confidence": 0.67,
                       "probabilities": {"last_action": 0.73, "utterance": 0.25,
                                         "unknown": 0.01, "last_command": 0.01}}
        return d, a, clock

    def decide(self, d, a):
        epoch = d.begin()
        result = d.decide("Read back the instruction that was actually sent.", a, epoch)
        self.assertIsNone(d.commit(result))
        return result

    def test_compatible_scope_split_reads_only_confirmed_source_verbatim(self):
        d, a, _ = self.fixture()
        source = d.last_action
        result = self.decide(d, a)
        self.assertEqual((result.op, result.reason, result.target), ("answer", "last_sent_request", "beta"))
        self.assertEqual(result.response, "exact_command")
        self.assertEqual(result.recorded_text, source.text)
        self.assertIs(d.last_action, source)
        self.assertIsNone(d.pending)

    def test_any_competing_agent_or_ambiguity_mass_prevents_source_fallback(self):
        for competitor in ("alpha", "stage", "ambiguous", "unlisted"):
            with self.subTest(competitor=competitor):
                d, a, _ = self.fixture()
                a["target"]["probabilities"][competitor] = 0.01
                a["target"]["probabilities"]["none"] -= 0.01
                result = self.decide(d, a)
                self.assertEqual(result.op, "clarify")
                self.assertEqual(result.recorded_text, "")

    def test_explicit_different_or_ambiguous_target_is_never_overridden(self):
        for target in ("alpha", "ambiguous", "unlisted"):
            with self.subTest(target=target):
                d, a, _ = self.fixture()
                a["target"] = choice(target)
                result = self.decide(d, a)
                self.assertNotEqual(result.op, "answer")
                self.assertEqual(result.recorded_text, "")

    def test_fresh_alternative_record_blocks_unique_source_fallback(self):
        for field in ("pending", "proposal", "last_command"):
            with self.subTest(field=field):
                d, a, clock = self.fixture()
                if field == "pending":
                    d.prepare("Run unrelated checks.", "alpha")
                else:
                    setattr(d, field, Record("Run unrelated checks.", "alpha", clock()))
                result = self.decide(d, a)
                self.assertEqual(result.op, "clarify")
                self.assertEqual(result.recorded_text, "")

    def test_missing_stale_future_unconfirmed_or_departed_source_cannot_be_read(self):
        for state in ("missing", "stale", "future", "waiting", "dispatching", "failed", "unknown", "departed", "empty"):
            with self.subTest(state=state):
                d, a, clock = self.fixture()
                if state == "missing":
                    d.last_action = None
                elif state == "stale":
                    d.last_action.created = clock() - 90.01
                elif state == "future":
                    d.last_action.created = clock() + 1
                elif state == "departed":
                    d.sync("alpha", [{"sessionId": "alpha"}])
                elif state == "empty":
                    d.last_action.text = " "
                else:
                    d.last_action.status = state
                result = self.decide(d, a)
                self.assertNotEqual(result.op, "answer")
                self.assertEqual(result.recorded_text, "")

    def test_independent_read_judgments_and_source_mass_are_required(self):
        for field in ("act", "route", "response", "source", "execute", "source_mass", "target_mass"):
            with self.subTest(field=field):
                d, a, _ = self.fixture()
                if field == "execute":
                    a[field] = {"noul": 0.11}
                elif field == "source_mass":
                    a["source"]["probabilities"] = {"last_action": 0.50, "utterance": 0.20, "unknown": 0.30}
                elif field == "target_mass":
                    a["target"]["probabilities"] = {"none": 0.30, "beta": 0.20, "previous": 0.10}
                else:
                    selection = a[field]["choice"]
                    a[field] = choice(selection, 0.40 if field == "source" else 0.70)
                result = self.decide(d, a)
                self.assertNotEqual(result.op, "answer")
                self.assertEqual(result.recorded_text, "")

    def test_low_confidence_source_never_borrows_this_read_contract_to_send(self):
        for act in ("direct", "confirm", "correct", "think", "ack"):
            with self.subTest(act=act):
                d, a, _ = self.fixture()
                a["act"] = choice(act)
                a["execute"] = {"noul": 0.99}
                result = self.decide(d, a)
                self.assertNotEqual(result.op, "dispatch")
                self.assertEqual(result.recorded_text, "")
                self.assertEqual(d.last_action.status, "sent")


class SentReadHandler(unittest.IsolatedAsyncioTestCase):
    async def test_grounded_sent_record_reaches_exact_speech_without_execution(self):
        from exact_speech import ExactSpeakFrame
        with ExitStack() as patches:
            for module in ("manager", "dialogue_manager", "memory_manager"):
                patches.enter_context(patch(module + ".emit", AsyncMock()))
            patches.enter_context(patch("manager.note"))
            run = patches.enter_context(patch("tools._run", AsyncMock()))
            native_run = patches.enter_context(patch("manager._run", AsyncMock()))
            m = make_manager()
            speech = SpeechEvidence(m)
            _, a, _ = SentReadGrounding().fixture()
            original = "python -m pytest tests/test_browser.py -q"
            m.dialogue.last_action = Record(original, "beta", m.dialogue.clock() - 4, "sent")
            m._jev.ask.return_value = a
            await m._turn("Read back the instruction that was actually sent.", None, None)
            self.assertEqual(len(speech.frames), 1)
            self.assertIsInstance(speech.frames[0], ExactSpeakFrame)
            self.assertEqual(speech.frames[0].text, original)
            self.assertEqual(m.dialogue.last_action.status, "sent")
            self.assertIsNone(m.dialogue.pending)
            m._brain.answer.assert_not_awaited()
            m._brain.compose_message.assert_not_awaited()
            run.assert_not_awaited()
            native_run.assert_not_awaited()
