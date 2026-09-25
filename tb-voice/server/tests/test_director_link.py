import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import director_link as d  # noqa: E402


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
