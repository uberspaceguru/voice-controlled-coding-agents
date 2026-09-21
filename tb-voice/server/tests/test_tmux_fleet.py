"""Synthetic tmux snapshots exercise the real read and speech handler boundaries."""

import json
import time
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from test_fleet_dialogue import judgment
from test_memory_manager import SpeechEvidence, make_manager

from fleet import FleetReadError, fleet_speech, parse_fleet
from manager import Manager

SOCKET = "/synthetic/tmux/default"


def agent(sid="alpha", name="Alpha", pid=101):
    return {"sessionId": sid, "name": name, "harness": "codex", "pid": pid,
            "status": "busy", "identityEvidence": ["live_process", "transcript_pid"]}


def pane(index=1, agents=None, status=None, socket=SOCKET, dead=False):
    agents = agents or []
    status = status or ("verified" if agents else "none")
    return {"id": f"{socket}:%{index}", "socketPath": socket,
            "sessionName": f"workspace {index}", "windowId": f"@{index}",
            "windowName": f"review {index}", "paneId": f"%{index}", "pid": 100 + index,
            "tty": f"/dev/ttys00{index}", "cwd": "/synthetic/repo",
            "command": "codex" if status != "none" else "zsh", "dead": dead,
            "attachedClientCount": 1, "agents": agents, "identityStatus": status,
            "candidateHarnesses": ["codex"] if status != "none" else []}


def snapshot(panes=(), servers=None, warnings=None):
    return {"schemaVersion": 1, "snapshotId": "synthetic-snapshot",
            "capturedAt": time.time(), "servers": servers if servers is not None else [
                {"socketPath": SOCKET, "status": "ok"}], "panes": list(panes),
            "warnings": warnings or []}


class TmuxFleetData(unittest.TestCase):
    def spoken(self, data, targets=(), **kwargs):
        return " ".join(fleet_speech(parse_fleet(data), list(targets), **kwargs))

    def test_verified_agents_shells_and_unresolved_panes_are_distinct(self):
        data = snapshot([pane(1, [agent()]), pane(2), pane(3, status="unresolved")])
        text = self.spoken(data)
        self.assertIn("1 live agent with verified identities in tmux", text)
        self.assertIn("3 live panes", text)
        self.assertIn("1 ordinary shell pane", text)
        self.assertIn("1 unidentified pane", text)
        self.assertIn("Agent 1: Alpha.", text)
        self.assertIn("Unidentified pane 1: session workspace 3, window review 3.", text)
        self.assertIn("Shell pane 1: session workspace 2, window review 2.", text)
        self.assertNotIn("3 live agents", text)

    def test_unresolved_candidate_is_not_an_agent_even_if_command_names_a_harness(self):
        data = snapshot([pane(status="unresolved")])
        text = self.spoken(data)
        self.assertIn("0 live agents with verified identities", text)
        self.assertIn("1 unidentified pane", text)
        self.assertNotIn("Agent 1:", text)

    def test_shell_only_snapshot_and_genuine_empty_snapshot_remain_available(self):
        for data, panes in ((snapshot([pane()]), 1), (snapshot(), 0),
                            (snapshot(servers=[]), 0)):
            with self.subTest(panes=panes):
                parsed = parse_fleet(data)
                self.assertFalse(parsed.partial)
                self.assertEqual(len(parsed.panes), panes)
                self.assertIn("0 live agents", self.spoken(data))

    def test_linked_panes_and_repeated_session_identity_do_not_double_count(self):
        first = pane(1, [agent()])
        linked = deepcopy(first)
        linked.update(sessionName="linked view", windowName="shared")
        data = snapshot([first, linked, pane(2, [agent(pid=202)])])
        parsed = parse_fleet(data)
        self.assertEqual(len(parsed.panes), 2)
        text = self.spoken(data)
        self.assertIn("1 live agent with verified identities", text)
        self.assertEqual(text.count("Agent 1: Alpha."), 1)
        self.assertNotIn("Agent 2:", text)

    def test_dead_panes_never_supply_live_agents(self):
        text = self.spoken(snapshot([pane(agents=[agent()], dead=True)]))
        self.assertIn("0 live agents", text)
        self.assertIn("0 live panes", text)
        self.assertIn("1 ended pane is excluded", text)
        self.assertNotIn("Alpha", text)

    def test_scan_failures_and_warnings_are_explicitly_partial(self):
        for data in (
            snapshot([pane(agents=[agent()])], servers=[
                {"socketPath": SOCKET, "status": "ok"},
                {"socketPath": "/synthetic/tmux/other", "status": "error", "error": "timeout"}]),
            snapshot([pane()], warnings=["process_scan_incomplete"]),
        ):
            with self.subTest(data=data):
                self.assertTrue(parse_fleet(data).partial)
                self.assertTrue(self.spoken(data).startswith("The tmux inventory is partial;"))

    def test_all_failed_scans_are_unavailable_instead_of_zero(self):
        for status in ("error", "unavailable", "skipped"):
            with self.subTest(status=status), self.assertRaises(FleetReadError):
                parse_fleet(snapshot(servers=[{"socketPath": SOCKET, "status": status}]))

    def test_unknown_schema_malformed_identity_and_impossible_server_rows_fail_closed(self):
        good = snapshot([pane(agents=[agent()])])
        invalid = [None, [], {}, {**good, "schemaVersion": 2},
                   {**good, "schemaVersion": True}, {**good, "capturedAt": float("nan")},
                   {**good, "servers": []}, {**good, "warnings": "not a list"}]
        for update in ({"agents": []}, {"identityStatus": "unresolved"},
                       {"dead": "false"}, {"candidateHarnesses": None}):
            item = deepcopy(good)
            item["panes"][0].update(update)
            invalid.append(item)
        evidence_missing = deepcopy(good)
        evidence_missing["panes"][0]["agents"][0]["identityEvidence"] = []
        invalid.append(evidence_missing)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(FleetReadError):
                parse_fleet(data)

    def test_known_metadata_enriches_matching_agents_without_inventing_a_total(self):
        targets = [
            {"sessionId": "alpha", "name": "Known Alpha", "status": "idle", "enrolled": True},
            {"sessionId": "beta", "name": "Other agent", "status": "busy", "enrolled": True},
        ]
        text = self.spoken(snapshot([pane(agents=[agent(name=None)])]), targets)
        for fragment in ("1 live agent with verified identities", "0 busy", "1 idle",
                         "1 enrolled", "Agent 1: Known Alpha.",
                         "1 other known live agent is not identified in this tmux inventory"):
            self.assertIn(fragment, text)
        self.assertNotIn("2 live agents", text)
        self.assertNotIn("outside tmux", text)
        self.assertNotIn("Agent 2:", text)

    def test_count_excludes_names_and_list_chunks_preserve_every_record(self):
        data = snapshot([pane(index, [agent(f"agent-{index}", f"Worker {index}", 100 + index)])
                         for index in range(1, 41)])
        parsed = parse_fleet(data)
        count = fleet_speech(parsed, [], include_names=False)
        self.assertNotIn("Worker", " ".join(count))
        self.assertIn("40 live agents", " ".join(count))
        chunks = fleet_speech(parsed, [])
        self.assertTrue(all(len(chunk.split()) <= 70 for chunk in chunks))
        for index in range(1, 41):
            self.assertIn(f"Worker {index}.", " ".join(chunks))

    def test_recorded_name_remains_preferred_over_a_pane_location(self):
        data = snapshot([pane(agents=[agent(name="Indexing checks")])])
        text = self.spoken(data)
        self.assertIn("Agent 1: Indexing checks.", text)
        self.assertNotIn("in session workspace", text)


class TmuxFleetHandlers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = ExitStack()
        for module in ("manager", "dialogue_manager", "memory_manager"):
            self.patches.enter_context(patch(module + ".emit", AsyncMock()))
        self.patches.enter_context(patch("manager.note"))
        self.send = self.patches.enter_context(patch("tools._run", AsyncMock()))
        self.read = self.patches.enter_context(patch("manager._run", AsyncMock()))

    def tearDown(self):
        self.send.assert_not_awaited()
        for call in self.read.await_args_list:
            self.assertEqual(call.args[1:], ("fleet", "--json"))
        self.patches.close()

    def fixture(self, data):
        m = make_manager()
        m.stage = None
        m._targets = AsyncMock(return_value=[])
        m.dialogue.sync(None, [])
        self.read.return_value = (0, json.dumps(data))
        m._jev.ask.return_value = judgment("fleet_inventory")
        return m, SpeechEvidence(m)

    @staticmethod
    def spoken(speech):
        return " ".join(frame.text for frame in speech.frames)

    def assert_read_only(self, m):
        self.assertIsNone(m.dialogue.last_action)
        self.assertIsNone(m.dialogue.pending)
        m._brain.answer.assert_not_awaited()
        m._brain.plain.assert_not_awaited()
        m._brain.compose_message.assert_not_awaited()
        m._brief.assert_not_awaited()

    async def test_real_fleet_door_finds_existing_tmux_agents_without_enrolling_or_targeting_them(self):
        m, speech = self.fixture(snapshot([
            pane(agents=[agent("external", "Existing agent")]), pane(2),
            pane(3, status="unresolved")]))
        await m._turn("Which agents already exist?", None, None)
        self.read.assert_awaited_once()
        text = self.spoken(speech)
        self.assertIn("1 live agent", text)
        self.assertIn("Existing agent", text)
        self.assertIn("0 enrolled", text)
        self.assertIn("workspace 3", text)
        self.assertEqual(m.dialogue.targets, {})
        self.assertIsNone(m.stage)
        self.assert_read_only(m)

    async def test_real_count_route_omits_agent_and_unresolved_pane_names(self):
        m, speech = self.fixture(snapshot([pane(agents=[agent()]), pane(2, status="unresolved")]))
        m._jev.ask.return_value = judgment("fleet_count")
        await m._turn("How many agents are running?", None, None)
        text = self.spoken(speech)
        self.assertIn("1 live agent", text)
        self.assertIn("1 unidentified pane", text)
        self.assertNotIn("Alpha", text)
        self.assertNotIn("workspace", text)
        self.assert_read_only(m)

    async def test_new_unnamed_agents_have_distinct_audible_locations_without_adoption(self):
        first = pane(86, [agent("external-one", name=None, pid=186)])
        first.update(sessionName="Y1-cdx", windowName="indexing")
        second = pane(87, [agent("external-two", name=None, pid=187)])
        second.update(sessionName="Y2-cdx", windowName="browser")
        m, speech = self.fixture(snapshot([first, second]))
        await m._turn("List the agents already running in tmux.", None, None)
        text = self.spoken(speech)
        self.assertIn("Agent 1: codex in session Y1-cdx, window indexing, pane 86.", text)
        self.assertIn("Agent 2: codex in session Y2-cdx, window browser, pane 87.", text)
        self.assertNotIn("unnamed codex agent", text)
        self.assertIn("2 live agents", text)
        self.assertIn("0 enrolled", text)
        self.assertEqual(m.dialogue.targets, {})
        self.assertIsNone(m.stage)
        self.read.assert_awaited_once()
        self.assert_read_only(m)

    async def test_failed_or_unsupported_read_has_no_silent_legacy_fallback(self):
        for result in ((2, "unknown command fleet"), (124, "timed out"),
                       (0, "bad json"), (0, "{}")):
            with self.subTest(result=result):
                m, speech = self.fixture(snapshot())
                self.read.reset_mock()
                self.read.return_value = result
                await m._handle_turn("How many agents are there?", None, None)
                self.read.assert_awaited_once()
                text = self.spoken(speech).lower()
                self.assertRegex(text, r"can't read|unavailable")
                self.assertNotRegex(text, r"\b0\b|\bzero\b")
                self.assert_read_only(m)

    async def test_partial_scan_speaks_qualified_count_and_complete_failure_is_unavailable(self):
        m, speech = self.fixture(snapshot([pane(agents=[agent()])], servers=[
            {"socketPath": SOCKET, "status": "ok"},
            {"socketPath": "/synthetic/tmux/second", "status": "unavailable"}]))
        await m._handle_turn("List the available agents.", None, None)
        self.assertIn("partial", self.spoken(speech))
        self.assertIn("1 live agent", self.spoken(speech))
        self.assert_read_only(m)
        self.read.reset_mock()
        m, speech = self.fixture(snapshot(servers=[{"socketPath": SOCKET, "status": "error"}]))
        await m._handle_turn("List the available agents.", None, None)
        self.assertIn("can't read", self.spoken(speech))
        self.assertNotRegex(self.spoken(speech), r"\b0\b")
        self.assert_read_only(m)

    async def test_interrupted_inventory_stops_before_remaining_chunks(self):
        m, _ = self.fixture(snapshot([pane(index, [agent(f"a-{index}", f"Worker {index}")])
                                     for index in range(1, 25)]))
        m._say = AsyncMock(return_value=False)
        await m._turn("List all the agent names.", None, None)
        m._say.assert_awaited_once()
        self.assert_read_only(m)

    async def test_read_method_returns_snapshot_not_dispatch_targets(self):
        m, _ = self.fixture(snapshot([pane()]))
        result = await Manager._tmux_fleet(m)
        self.assertEqual(result.snapshot_id, "synthetic-snapshot")
        self.assertEqual(result.panes[0]["identityStatus"], "none")
        self.assertEqual(m.dialogue.targets, {})


if __name__ == "__main__":
    unittest.main()
