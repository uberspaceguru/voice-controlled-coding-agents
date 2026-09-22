"""Mock the subprocess protocol, not the persistent session or validation policy."""

import asyncio
import json
import os
import signal
import stat
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

from supervisor_session import (
    DISABLED_FEATURES,
    STARTUP_NOTICE,
    SupervisorError,
    SupervisorSession,
    child_environment,
    prepare_context,
    response_schema,
    validate_plan,
)

THREAD = "12345678-1234-4234-8234-123456789abc"
OTHER_THREAD = "12345678-1234-4234-8234-123456789abd"


def context(request="one"):
    return {
        "request_id": request, "user_text": "What is Alpha working on?",
        "observations": [
            {"id": "brief-alpha", "kind": "brief", "session_id": "alpha", "observed_at": 1000,
             "data": {"goal": "Review indexing", "status": "working"}},
            {"id": "brief-beta", "kind": "brief", "session_id": "beta", "observed_at": 1001,
             "data": {"goal": "Review rendering"}},
        ],
        "candidates": [
            {"session_id": "alpha", "name": "Alpha", "cwd": "/synthetic/alpha", "harness": "codex",
             "status": "busy", "socket_path": "/synthetic/tmux", "pane_id": "%1"},
            {"session_id": "beta", "name": "Beta", "cwd": "/synthetic/beta", "harness": "codex",
             "status": "idle", "socket_path": "/synthetic/tmux", "pane_id": "%2"},
        ],
        "message_sources": [{"id": "user-one", "text": "Run the indexing tests."}],
        "route": "manager_question", "source": "utterance", "history": [],
        "allowed_operations": ["answer", "clarify"], "allowed_target_ids": ["alpha", "beta"],
    }


def plan(**updates):
    result = {"reply": "Alpha is reviewing indexing, according to its recorded goal.",
              "operation": "answer", "target_session_id": "alpha", "message_source": None,
              "evidence_ids": ["brief-alpha"], "work_notes": [
                  {"session_id": "alpha", "summary": "The current goal concerns indexing.",
                   "evidence_ids": ["brief-alpha"]}]}
    result.update(updates)
    return result


def events(thread=THREAD, *, item=None, complete=True, notice=False):
    rows = [{"type": "thread.started", "thread_id": thread}]
    if notice:
        rows.append({"type": "item.completed", "item": {"id": "notice", "type": "error",
                                                        "message": STARTUP_NOTICE}})
    rows.append({"type": "turn.started"})
    if item:
        rows.append({"type": "item.completed", "item": item})
    rows.append({"type": "item.completed", "item": {"type": "agent_message", "text": "structured response"}})
    if complete:
        rows.append({"type": "turn.completed"})
    return rows


class Stdin:
    def __init__(self):
        self.prompt = b""

    def write(self, data):
        self.prompt += data

    async def drain(self):
        pass

    def close(self):
        pass


class Process:
    def __init__(self, rows, code=0, blocked=False):
        self.pid = 987654
        self.returncode = None if blocked else code
        self.stdin = Stdin()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.exited = asyncio.Event()
        for row in rows:
            self.stdout.feed_data((json.dumps(row) + "\n").encode())
        self.stderr.feed_eof()
        if not blocked:
            self.stdout.feed_eof()
            self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def finish(self):
        self.returncode = -signal.SIGTERM
        self.stdout.feed_eof()
        self.exited.set()


class SupervisorValidation(unittest.TestCase):
    def test_allowed_source_backed_selection_contains_no_generated_command(self):
        data = context()
        data["allowed_operations"] = ["send"]
        selected = validate_plan(plan(operation="send", message_source="user-one"), data)
        self.assertEqual(selected.message_source, "user-one")
        self.assertNotIn("text", selected.as_dict())
        self.assertEqual(selected.work_notes[0]["evidence_ids"], ["brief-alpha"])

    def test_read_question_cannot_request_action_even_with_known_target_and_source(self):
        for op in ("send", "focus"):
            with self.subTest(op=op), self.assertRaises(SupervisorError) as raised:
                validate_plan(plan(operation=op, message_source="user-one" if op == "send" else None), context())
            self.assertEqual(raised.exception.code, "invalid_plan")

    def test_unknown_stale_and_missing_action_references_are_rejected(self):
        data = context()
        data["allowed_operations"] = ["send", "focus", "answer", "clarify"]
        for updates in (
            {"target_session_id": "old-agent"}, {"message_source": "generated-command"},
            {"operation": "send", "target_session_id": None, "message_source": "user-one"},
            {"operation": "send", "message_source": None},
            {"operation": "focus", "message_source": "user-one"},
            {"evidence_ids": ["old-observation"]}, {"evidence_ids": ["brief-alpha", "brief-alpha"]},
            {"command": "invented executable field"},
        ):
            with self.subTest(updates=updates), self.assertRaises(SupervisorError):
                validate_plan(plan(**updates), data)

    def test_work_notes_cannot_turn_another_agents_observation_into_this_agents_memory(self):
        for note in (
            {"session_id": "alpha", "summary": "Done", "evidence_ids": ["brief-beta"]},
            {"session_id": "alpha", "summary": "Done", "evidence_ids": []},
            {"session_id": "missing", "summary": "Done", "evidence_ids": ["brief-alpha"]},
        ):
            with self.subTest(note=note), self.assertRaises(SupervisorError):
                validate_plan(plan(work_notes=[note]), context())

    def test_context_is_frozen_and_rejects_duplicates_before_launch(self):
        source = context()
        frozen = prepare_context(source)
        source["observations"][0]["data"]["goal"] = "Changed after submission"
        self.assertEqual(frozen["observations"][0]["data"]["goal"], "Review indexing")
        for field in ("candidates", "observations", "message_sources"):
            data = context()
            data[field].append(deepcopy(data[field][0]))
            with self.subTest(field=field), self.assertRaises(SupervisorError):
                prepare_context(data)

    def test_empty_fleet_schema_does_not_create_an_invalid_empty_enum(self):
        data = context()
        data.update(candidates=[], allowed_target_ids=[], observations=[], message_sources=[])
        schema = response_schema(prepare_context(data))
        self.assertNotIn('"enum": []', json.dumps(schema))
        self.assertEqual(schema["properties"]["work_notes"]["maxItems"], 0)
        self.assertEqual(validate_plan(plan(target_session_id=None, evidence_ids=[], work_notes=[]), data).operation, "answer")

    def test_supplied_memory_and_stage_are_context_only_not_extra_permission(self):
        data = context()
        data.update(work_memory={"alpha": {"status": "sent", "source_id": "old-receipt"}},
                    inferred_notes=[{"session_id": "alpha", "summary": "Possibly indexing"}],
                    stage={"session_id": "alpha"})
        frozen = prepare_context(data)
        self.assertEqual(frozen["work_memory"]["alpha"]["status"], "sent")
        with self.assertRaises(SupervisorError):
            validate_plan(plan(operation="send", message_source="user-one"), frozen)

    def test_environment_reuses_account_home_but_excludes_keys_and_worker_routing(self):
        with patch.dict(os.environ, {"CODEX_HOME": "/synthetic/account", "CODEX_API_KEY": "synthetic",
                                     "OPENAI_API_KEY": "synthetic", "TMUX": "synthetic-pane",
                                     "OPENAI_BASE_URL": "https://invalid.example", "LD_PRELOAD": "synthetic"}):
            environment = child_environment()
        self.assertEqual(environment["CODEX_HOME"], "/synthetic/account")
        for field in ("CODEX_API_KEY", "OPENAI_API_KEY", "TMUX", "OPENAI_BASE_URL", "LD_PRELOAD"):
            self.assertNotIn(field, environment)


class SupervisorProvider(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "manager"
        self.rows = events()
        self.response = plan()
        self.code = 0
        self.blocked = False
        self.processes = []
        self.commands = []
        self.spawn = patch("supervisor_session.asyncio.create_subprocess_exec", new=AsyncMock(side_effect=self.create))
        self.mock_spawn = self.spawn.start()
        self.addCleanup(self.spawn.stop)
        self.kill = patch("supervisor_session.os.killpg", side_effect=self.stop)
        self.mock_kill = self.kill.start()
        self.addCleanup(self.kill.stop)

    async def create(self, *command, **kwargs):
        self.commands.append((command, kwargs))
        response_path = Path(command[command.index("--output-last-message") + 1])
        response_path.write_text(self.response if isinstance(self.response, str) else json.dumps(self.response))
        process = Process(self.rows, self.code, self.blocked)
        self.processes.append(process)
        return process

    def stop(self, pid, sig):
        self.assertEqual(pid, 987654)
        self.assertIn(sig, (signal.SIGTERM, signal.SIGKILL))
        self.processes[-1].finish()

    async def test_two_turns_and_a_new_wrapper_resume_one_actual_thread_with_fresh_schema(self):
        first = SupervisorSession(self.root)
        result = await first.turn(context())
        self.assertEqual(result.operation, "answer")
        self.assertEqual(first.thread_id, THREAD)
        self.assertNotIn("resume", self.commands[0][0])
        second = SupervisorSession(self.root)
        data = context("two")
        data["history"] = [{"kind": "receipt", "session_id": "alpha", "status": "sent", "source_id": "user-one"}]
        await second.turn(data)
        command = self.commands[1][0]
        self.assertEqual(command[-3:], ("resume", THREAD, "-"))
        self.assertNotIn("--last", command)
        self.assertIn('"status": "sent"', self.processes[1].stdin.prompt.decode())
        state = json.loads((self.root / "session.json").read_text())
        self.assertEqual(state["thread_id"], THREAD)
        self.assertEqual([item["status"] for item in state["requests"].values()], ["completed", "completed"])
        for path in self.root.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    async def test_every_turn_reapplies_tool_and_auth_boundaries_and_never_executes_the_plan(self):
        data = context()
        data["allowed_operations"] = ["send"]
        self.response = plan(operation="send", message_source="user-one")
        result = await SupervisorSession(self.root).turn(data)
        self.assertEqual(result.operation, "send")
        self.mock_spawn.assert_awaited_once()
        command, kwargs = self.commands[0]
        self.assertEqual(command[:2], ("codex", "exec"))
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("--ignore-user-config", command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn('web_search="disabled"', command)
        self.assertNotIn("--ephemeral", command)
        self.assertTrue(kwargs["start_new_session"])
        for feature in DISABLED_FEATURES:
            self.assertIn(("--disable", feature), [command[i:i + 2] for i in range(len(command) - 1)])

    async def test_failed_completed_malformed_and_wrong_thread_outputs_never_become_plans(self):
        cases = [
            (events(complete=False), plan(), 0, "provider_failed"),
            (events(), plan(), 2, "provider_failed"),
            (events(), "{bad json", 0, "invalid_output"),
            (events(), plan(operation="send", message_source="user-one"), 0, "invalid_plan"),
        ]
        for index, (rows, response, code, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                self.rows, self.response, self.code = rows, response, code
                root = self.root / f"case-{index}"
                with self.assertRaises(SupervisorError) as raised:
                    await SupervisorSession(root).turn(context())
                self.assertEqual(raised.exception.code, expected)
                self.assertFalse(list(root.glob("turn-*/plan.json")))
                saved = json.loads((root / "session.json").read_text())
                self.assertEqual(saved["thread_id"], THREAD)
                self.assertEqual(saved["requests"]["one"]["status"], "failed")

    async def test_a_resume_cannot_silently_switch_to_another_conversation(self):
        engine = SupervisorSession(self.root)
        await engine.turn(context())
        self.rows = events(thread=OTHER_THREAD)
        with self.assertRaises(SupervisorError) as raised:
            await engine.turn(context("two"))
        self.assertEqual(raised.exception.code, "thread_mismatch")
        self.assertEqual(json.loads((self.root / "session.json").read_text())["thread_id"], THREAD)

    async def test_duplicate_request_after_success_is_not_inferred_or_dispatched_again(self):
        engine = SupervisorSession(self.root)
        await engine.turn(context())
        with self.assertRaises(SupervisorError) as raised:
            await engine.turn(context())
        self.assertEqual(raised.exception.code, "duplicate_request")
        self.mock_spawn.assert_awaited_once()

    async def test_duplicate_json_keys_and_extra_execution_fields_fail_independent_validation(self):
        for index, response in enumerate((
            '{"reply":"safe","reply":"different"}',
            plan(command="invented shell execution"),
        )):
            self.response = response
            root = self.root / str(index)
            with self.subTest(index=index), self.assertRaises(SupervisorError):
                await SupervisorSession(root).turn(context())
            self.assertFalse(list(root.glob("turn-*/plan.json")))

    async def test_tool_use_or_unknown_item_is_rejected_even_with_valid_response_json(self):
        for index, kind in enumerate(("command_execution", "mcp_tool_call", "file_change", "web_search", "unknown_tool")):
            self.rows = events(item={"type": kind})
            with self.subTest(kind=kind), self.assertRaises(SupervisorError) as raised:
                await SupervisorSession(self.root / str(index)).turn(context())
            self.assertEqual(raised.exception.code, "tool_use")

    async def test_observed_disabled_host_notice_is_accepted_only_before_turn_start(self):
        self.rows = events(notice=True)
        self.assertEqual((await SupervisorSession(self.root).turn(context())).operation, "answer")
        self.rows = events(item={"type": "error", "message": STARTUP_NOTICE})
        with self.assertRaises(SupervisorError) as raised:
            await SupervisorSession(self.root).turn(context("two"))
        self.assertEqual(raised.exception.code, "provider_failed")

    async def test_cancellation_checkpoints_thread_and_cannot_retry_same_request(self):
        engine = SupervisorSession(self.root)
        self.rows, self.blocked = events(complete=False), True
        task = asyncio.create_task(engine.turn(context()))
        for _ in range(30):
            if engine.thread_id:
                break
            await asyncio.sleep(0)
        self.assertEqual(engine.thread_id, THREAD)
        await engine.cancel()
        self.assertTrue(task.cancelled())
        self.mock_kill.assert_called_once_with(987654, signal.SIGTERM)
        state = json.loads((self.root / "session.json").read_text())
        self.assertEqual(state["requests"]["one"]["status"], "canceled")
        with self.assertRaises(SupervisorError) as raised:
            await SupervisorSession(self.root).turn(context())
        self.assertEqual(raised.exception.code, "duplicate_request")
        self.mock_spawn.assert_awaited_once()

    async def test_timeout_stops_only_child_and_returns_no_plan(self):
        engine = SupervisorSession(self.root)
        engine.timeout = 0.015
        self.rows, self.blocked = events(complete=False), True
        with self.assertRaises(SupervisorError) as raised:
            await engine.turn(context())
        self.assertEqual(raised.exception.code, "timeout")
        self.mock_kill.assert_called_once_with(987654, signal.SIGTERM)
        self.assertFalse(list(self.root.glob("turn-*/plan.json")))

    async def test_concurrent_requests_serialize_and_second_uses_saved_thread(self):
        engine = SupervisorSession(self.root)
        await asyncio.gather(engine.turn(context("one")), engine.turn(context("two")))
        self.assertEqual(len(self.commands), 2)
        self.assertNotIn("resume", self.commands[0][0])
        self.assertEqual(self.commands[1][0][-3:], ("resume", THREAD, "-"))

    async def test_another_engine_cannot_write_same_thread_while_a_turn_is_active(self):
        first = SupervisorSession(self.root)
        self.rows, self.blocked = events(complete=False), True
        task = asyncio.create_task(first.turn(context()))
        for _ in range(30):
            if first.thread_id:
                break
            await asyncio.sleep(0)
        try:
            with self.assertRaises(SupervisorError) as raised:
                await SupervisorSession(self.root).turn(context("two"))
            self.assertEqual(raised.exception.code, "busy")
            self.mock_spawn.assert_awaited_once()
        finally:
            await first.cancel()
        self.assertTrue(task.cancelled())

    async def test_invalid_context_or_nonprivate_state_never_starts_a_provider(self):
        invalid = context()
        invalid["allowed_target_ids"] = ["missing"]
        with self.assertRaises(SupervisorError):
            await SupervisorSession(self.root).turn(invalid)
        self.assertFalse(self.root.exists())
        self.root.mkdir(mode=0o755)
        with self.assertRaises(SupervisorError) as raised:
            await SupervisorSession(self.root).turn(context())
        self.assertEqual(raised.exception.code, "unsafe_state")
        self.mock_spawn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
