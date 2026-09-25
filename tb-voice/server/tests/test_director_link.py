import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import director_link as d  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402

# A roster of its own, so no test reads the Mac's right-hands.json.
_ROSTER = os.path.join(tempfile.mkdtemp(), "right-hands.json")
with open(_ROSTER, "w") as _fh:
    json.dump({"hands": [
        {"name": "Director", "session": "ac03daf5", "ask": ["director", "ask", "{text}", "--channel", "tranquility",
                                                            "--external-id", "{conversation}"]},
        {"name": "Yobi1", "session": "e781aff1", "ask": ["~/brains/yobi1-ask", "{text}"]},
        {"name": "Sys-3PO", "session": "2b973845", "ask": ["/opt/brains/sys3po-ask", "{text}"]},
        {"name": "TeamChat Manager"},
    ]}, _fh)
d.ROSTER = _ROSTER
os.environ.pop("TB_RIGHT_HAND_CARDS", None)


class Route(unittest.TestCase):
    def test_the_vocative_goes_to_director(self):
        self.assertEqual(d.route("Director, what needs me?"), ("ask", "what needs me?"))
        self.assertEqual(d.route("director. tell the Wispr worker yes"), ("ask", "tell the Wispr worker yes"))
        self.assertEqual(d.route("Hey Director"), ("ask", "what needs me?"))

    def test_what_needs_me_goes_to_director_without_the_name(self):
        for t in ("What needs me?", "what's ready", "Is anything ready for me?",
                  "What's waiting on me right now?", "catch me up"):
            self.assertEqual(d.route(t), ("ask", t), t)

    def test_telling_an_agent_goes_to_director(self):
        self.assertEqual(d.route("Tell the Wispr worker yes"), ("ask", "Tell the Wispr worker yes"))
        self.assertEqual(d.route("ask Yobi1 to pause the backfill"), ("ask", "ask Yobi1 to pause the backfill"))

    def test_who_are_you_is_answered_by_tranquility(self):
        for t in ("Are you the director?", "Who are you?", "are you the fleet manager"):
            self.assertEqual(d.route(t), ("identity", ""), t)
        self.assertNotIn("fleet manager", d.IDENTITY_LINE.lower())
        self.assertIn("Tranquility", d.IDENTITY_LINE)

    def test_everything_else_is_left_to_the_dialogue(self):
        for t in ("invite the next agent", "what did it find", "stop", "", "the director of engineering said no",
                  "Ask Alpha to stop its current task.", "tell it to cancel"):
            self.assertIsNone(d.route(t), t)


class Speaking(unittest.TestCase):
    def test_the_reply_is_flattened_not_reworded(self):
        reply = "Waiting on you:\n1. w-a17: needs a Granola key\n2. y1-cc: The backfill runs on its own"
        self.assertEqual(d.flatten(reply),
                         "Waiting on you: 1: w-a17: needs a Granola key. 2: y1-cc: The backfill runs on its own.")

    def test_long_lines_split_at_sentences(self):
        line = " ".join(["One two three four five."] * 30)
        parts = d.chunks(line, 20)
        self.assertTrue(all(len(p.split()) <= 20 for p in parts))
        self.assertEqual(" ".join(parts), line)

    def test_the_ask_command(self):
        argv = d.ask_argv("what needs me?", "tranquility:voice")
        self.assertEqual(argv[1:], ["ask", "what needs me?", "--channel", "tranquility",
                                    "--external-id", "tranquility:voice"])


class Inventory(unittest.TestCase):
    def test_the_fleet_is_directors(self):
        status = {"groups": {"needs_you": [{}] * 10, "working": [{}] * 7, "stuck": [{}] * 2, "idle": [{}] * 16}}
        line = d.inventory(status, ["Director", "Yobi1", "Sys-3PO", "TeamChat Manager"])
        self.assertEqual(line, "Your right-hands are Director, Yobi1, Sys-3PO and TeamChat Manager. "
                               "Director is tracking the rest: 10 need you, 7 working, 2 stuck, 16 idle.")
        self.assertEqual(d.inventory({"groups": {"needs_you": [{}]}}, []),
                         "Director is tracking the rest: 1 needs you.")



class ByName(unittest.TestCase):
    """Each right-hand is talked to by name (25 Sep)."""

    def test_a_hand_named_first_gets_the_words(self):
        for t, want in (("Yobi one, what's on today?", ("hand", "Yobi1", "what's on today?")),
                        ("Yobi1 what do I have today", ("hand", "Yobi1", "what do I have today")),
                        ("Hey Yobi-1", ("hand", "Yobi1", "what needs me?")),
                        ("Sys three P O, how's the Mac?", ("hand", "Sys-3PO", "how's the Mac?")),
                        ("C-3PO, is the disk ok?", ("hand", "Sys-3PO", "is the disk ok?")),
                        ("Sys-3PO, check the fleet", ("hand", "Sys-3PO", "check the fleet")),
                        ("TeamChat Manager, anything?", ("hand", "TeamChat Manager", "anything?"))):
            self.assertEqual(d.route(t), want, t)

    def test_director_and_instructions_stay_directors(self):
        self.assertEqual(d.route("Director, what needs me?"), ("ask", "what needs me?"))
        self.assertEqual(d.route("ask Yobi1 to pause the backfill"), ("ask", "ask Yobi1 to pause the backfill"))
        for t in ("Siri, what time is it", "so three people came", "yo buddy what's up"):
            self.assertIsNone(d.route(t), t)

    def test_a_hands_ask_is_filled_as_one_argument(self):
        argv = d.hand_argv(d.hand_named("Yobi1"), "what's on; rm -rf ~")
        self.assertEqual(argv, [os.path.expanduser("~/brains/yobi1-ask"), "what's on; rm -rf ~"])
        self.assertEqual(d.hand_argv(d.hand_named("Director"), "hi")[1:],
                         ["ask", "hi", "--channel", "tranquility", "--external-id", "ac03daf5"])
        self.assertIsNone(d.hand_argv(d.hand_named("TeamChat Manager"), "hi"))

    def test_director_default_routes_everything_but_a_name_or_stop(self):
        for t, want in (("yes", ("ask", "yes")), ("the second one", ("ask", "the second one")),
                        ("Director, what needs me?", ("ask", "what needs me?")),
                        ("Yobi1, what's my day?", ("hand", "Yobi1", "what's my day?")),
                        ("stop", ("mute", "")), ("Okay, stop.", ("mute", "")), ("", None), ("...", None)):
            self.assertEqual(d.route_default(t), want, t)

    def test_the_roster_is_the_host_apps(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"VOICE_DISPATCH_SUPPORT_DIR": "/x/VoiceDispatch-Director"}, clear=False):
            os.environ.pop("TB_RIGHT_HANDS", None)
            self.assertEqual(d._roster_path(), "/x/VoiceDispatch-Director/right-hands.json")


if __name__ == "__main__":
    unittest.main()


class Wiring(unittest.IsolatedAsyncioTestCase):
    """The manager's half: Director's turn skips the judgment, its answer is
    spoken as it came back, and the fleet is Director's."""

    async def asyncSetUp(self):
        from unittest.mock import AsyncMock, patch
        from test_memory_manager import make_manager
        self.m = make_manager()
        self.m._say = AsyncMock(return_value=True)
        self.patches = [patch("manager.note"), patch("dialogue_manager.emit", AsyncMock()),
                        patch("manager.emit", AsyncMock())]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()

    async def test_a_director_turn_is_asked_and_spoken_verbatim(self):
        from unittest.mock import AsyncMock, patch
        reply = "Waiting on you:\n1. w-a17: needs a Granola key"
        with patch("manager._run", AsyncMock(return_value=(0, reply))) as run:
            await self.m._dialogue_turn("Director, what needs me?", None, None)
        argv = run.await_args.args
        self.assertEqual(argv[1:], ("ask", "what needs me?", "--channel", "tranquility",
                                    "--external-id", d.THREAD))
        self.m._jev.ask.assert_not_awaited()
        said = [c.args[0] for c in self.m._say.await_args_list]
        self.assertEqual(said, ["Director: Waiting on you: 1: w-a17: needs a Granola key."])

    async def test_a_hand_answers_in_its_own_name(self):
        from unittest.mock import AsyncMock, patch
        with patch("manager._run", AsyncMock(return_value=(0, "The Mac is fine: pressure normal."))) as run:
            await self.m._dialogue_turn("Sys-3PO, how's the Mac?", None, None)
        self.assertEqual(run.await_args.args, ("/opt/brains/sys3po-ask", "how's the Mac?"))
        self.m._jev.ask.assert_not_awaited()
        self.assertEqual([c.args[0] for c in self.m._say.await_args_list],
                         ["Sys-3PO: The Mac is fine: pressure normal."])

    async def test_in_the_director_app_the_card_answers(self):
        from unittest.mock import AsyncMock, patch
        self.m._card_secs = lambda reply: 0
        replies = AsyncMock(side_effect=[(0, "You have two meetings today."), (0, "Ahmed, nine things need you.")])
        with patch.dict(os.environ, {"TB_RIGHT_HAND_CARDS": "1"}), \
                patch("manager._run", replies) as run, patch("manager.emit", AsyncMock()) as emit:
            await self.m._dialogue_turn("Yobi one, what's on today?", None, None)
            await self.m._dialogue_turn("Director, what needs me?", None, None)
        self.assertEqual(run.await_args_list[0].args, (os.path.expanduser("~/brains/yobi1-ask"), "what's on today?"))
        self.assertEqual(run.await_args_list[1].args[-1], "ac03daf5", "the Director card's own thread")
        self.m._say.assert_not_awaited()
        answers = [c.kwargs for c in emit.await_args_list if c.args[1:] == ("answer",)]
        self.assertEqual(answers, [{"session": "e781aff1", "name": "Yobi1", "text": "You have two meetings today."},
                                   {"session": "ac03daf5", "name": "Director", "text": "Ahmed, nine things need you."}])

    async def test_hands_free_in_the_director_app_talks_to_director(self):
        from unittest.mock import AsyncMock, patch
        self.m._card_secs = lambda reply: 0
        run = AsyncMock(return_value=(0, "Done."))
        env = {"TB_RIGHT_HAND_CARDS": "1", "TB_DEFAULT_INTERLOCUTOR": "director"}
        with patch.dict(os.environ, env), patch("manager._run", run), patch("manager.emit", AsyncMock()):
            for t in ("yes", "the second one", "tell the Wispr worker yes", "what's the weather like"):
                await self.m._dialogue_turn(t, None, None)
            self.m.broadcast_interruption = AsyncMock()
            self.m._do_mute = AsyncMock()
            await self.m._dialogue_turn("stop", None, None)
            await self.m._dialogue_turn("   ", None, None)
        asked = [(c.args[2], c.args[-1]) for c in run.await_args_list]
        self.assertEqual(asked, [("yes", "ac03daf5"), ("the second one", "ac03daf5"),
                                 ("tell the Wispr worker yes", "ac03daf5"), ("what's the weather like", "ac03daf5")],
                         "every utterance, one thread")
        self.m._jev.ask.assert_not_awaited()
        self.m._do_mute.assert_awaited_once()

    async def test_a_placeholder_says_so(self):
        await self.m._dialogue_turn("TeamChat Manager, anything?", None, None)
        self.assertEqual(self.m._say.await_args.args[0], "TeamChat Manager isn't connected yet.")

    async def test_who_are_you_never_says_fleet_manager(self):
        await self.m._dialogue_turn("Are you the director?", None, None)
        said = self.m._say.await_args.args[0]
        self.assertIn("Tranquility", said)
        self.assertNotIn("fleet manager", said.lower())

    async def test_the_fleet_is_directors(self):
        from unittest.mock import AsyncMock
        self.m._director_inventory = AsyncMock(return_value="Your right-hands are Director and Yobi1.")
        await self.m._fleet_inventory()
        self.assertEqual(self.m._say.await_args.args[0], "Your right-hands are Director and Yobi1.")
        self.m._targets.assert_not_awaited()
