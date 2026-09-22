"""Unit tests for harness.agent.loop — stdlib unittest only.

A FakeTransport serves scripted (content, tool_calls) turns so the loop
is tested without any model server.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness.agent.loop import AgentLoop
from harness.tools import ToolContext, ToolRegistry
from harness.tools.builtin import register_all


class FakeTransport:
    """Scripted transport. Each script entry is (content, tool_calls)
    or an Exception instance to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.payloads = []  # messages actually sent (for pruning assertions)
        self.tools_seen = []

    def chat(self, messages, tools):
        self.payloads.append(messages)
        self.tools_seen.append(tools)
        if not self.script:
            return "done", []
        entry = self.script.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        content, calls = entry
        return content, calls


def _tool_call(name, arguments=None, call_id="c1"):
    return {"id": call_id, "name": name, "arguments": arguments or {}}


def _make_loop(tmpdir, script, config=None, skills=None):
    ctx = ToolContext(project_root=tmpdir)
    registry = ToolRegistry()
    register_all(registry, ctx)
    transport = FakeTransport(script)
    loop = AgentLoop(
        transport=transport,
        model="fake-model",
        tools=registry,
        skills=skills,
        config=config or {},
        project_root=tmpdir,
        tool_context=ctx,
    )
    return loop, transport, ctx, registry


class TestAgentLoopBasics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "hello.txt").write_text("hello world")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tool_execution_then_final_answer(self):
        script = [
            ("Let me read the file.", [_tool_call("read_file", {"path": "hello.txt"})]),
            ("The file says hello world.", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("read hello.txt")
        self.assertEqual(result["stopped_reason"], "final_answer")
        self.assertEqual(result["result"], "The file says hello world.")
        self.assertEqual(result["steps"], 2)
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "read_file")
        self.assertTrue(result["tool_calls"][0]["ok"])
        # The tool result reached the model on the next turn.
        tool_msgs = [m for m in transport.payloads[1] if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("hello world", tool_msgs[0]["content"])

    def test_unknown_tool_becomes_error_result(self):
        script = [
            ("Calling something unknown.", [_tool_call("nope_not_real", {})]),
            ("Understood, it failed.", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("try unknown")
        self.assertEqual(result["stopped_reason"], "final_answer")
        tool_msgs = [m for m in transport.payloads[1] if m.get("role") == "tool"]
        self.assertIn("not registered", tool_msgs[0]["content"])

    def test_max_steps_stops_honestly(self):
        script = [("again", [_tool_call("list_dir", {})])] * 10
        loop, transport, ctx, registry = _make_loop(
            self.tmp.name, script, config={"max_steps": 3}
        )
        result = loop.run("loop forever")
        self.assertEqual(result["stopped_reason"], "max_steps")
        self.assertEqual(result["steps"], 3)
        self.assertEqual(len(result["tool_calls"]), 3)
        self.assertIn("Stopped after 3 steps", result["result"])

    def test_transport_error_reported(self):
        loop, transport, ctx, registry = _make_loop(
            self.tmp.name, [RuntimeError("connection refused")]
        )
        result = loop.run("hi")
        self.assertEqual(result["stopped_reason"], "transport_error")
        self.assertIn("connection refused", result["result"])

    def test_malformed_markup_retries_then_continues(self):
        script = [
            ("<tool_call>{\"name\": \"read_file\"}</tool_call>", []),
            ("Fine, no markup this time.", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("do it")
        self.assertEqual(result["stopped_reason"], "final_answer")
        # The corrective user message was injected before the retry.
        corrective = [
            m for m in transport.payloads[1]
            if m.get("role") == "user" and "NOT executed" in m.get("content", "")
        ]
        self.assertEqual(len(corrective), 1)

    def test_malformed_markup_gives_up_after_retries(self):
        script = [("<function=name>", [])] * 5
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("do it")
        self.assertEqual(result["stopped_reason"], "malformed_tools")
        self.assertIn("NOT executed", result["result"])

    def test_json_fallback_executes_registered_tool(self):
        script = [
            ('```json\n{"name": "list_dir", "arguments": {"path": "."}}\n```', []),
            ("Listed.", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("list")
        self.assertEqual(result["stopped_reason"], "final_answer")
        self.assertEqual(result["tool_calls"][0]["name"], "list_dir")
        self.assertTrue(result["tool_calls"][0]["ok"])

    def test_tool_message_ids_match_assistant_ids(self):
        # Native call without an id (and the JSON fallback) must still
        # produce tool messages whose tool_call_id matches the assistant
        # message's tool_calls entry.
        script = [
            ("native, no id", [{"name": "list_dir", "arguments": {}}]),
            ('```json\n{"name": "list_dir", "arguments": {}}\n```', []),
            ("done", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        loop.run("go")
        for payload in transport.payloads[1:]:
            current_ids = set()
            checked = 0
            for m in payload:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    current_ids = {c["id"] for c in m["tool_calls"]}
                    self.assertTrue(current_ids)
                elif m.get("role") == "tool":
                    self.assertIn(m["tool_call_id"], current_ids)
                    checked += 1
            self.assertGreater(checked, 0)

    def test_legacy_plain_schema_uses_registry_name(self):
        loop, transport, ctx, registry = _make_loop(self.tmp.name, [])
        registry.register(
            "legacy_tool", "A legacy plain-schema tool.",
            {"type": "object", "properties": {"q": {"type": "string"}}},
            lambda args: {"ok": True},
        )
        tools = loop._ollama_tools()
        legacy = [t for t in tools
                  if t["function"]["name"] == "legacy_tool"]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["function"]["description"],
                         "A legacy plain-schema tool.")
        # No generic "tool" placeholder names leak into the payload.
        self.assertNotIn("tool", [t["function"]["name"] for t in tools])

    def test_json_fallback_ignores_unknown_tools(self):
        script = [
            ('```json\n{"name": "definitely_not_a_tool", "arguments": {}}\n```', []),
            ("No tools then.", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("go")
        self.assertEqual(result["stopped_reason"], "final_answer")
        self.assertEqual(result["tool_calls"], [])


class TestContextBudgeting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_system_prompt_always_pinned(self):
        script = [("x", [_tool_call("list_dir", {})])] * 4
        loop, transport, ctx, registry = _make_loop(
            self.tmp.name,
            script,
            config={"max_steps": 4, "context_budget_chars": 500,
                    "history_keep_turns": 1},
        )
        loop.run("go")
        for payload in transport.payloads:
            self.assertEqual(payload[0]["role"], "system")

    def test_old_turns_pruned_when_over_budget(self):
        script = [("pad " + "y" * 2000, [_tool_call("list_dir", {})])] * 8
        loop, transport, ctx, registry = _make_loop(
            self.tmp.name,
            script,
            config={"max_steps": 8, "context_budget_chars": 3000,
                    "history_keep_turns": 2, "tool_output_limit": 100},
        )
        loop.run("go")
        last_payload = transport.payloads[-1]
        total = sum(len(json.dumps(m)) for m in last_payload)
        # Budget is approximate, but pruning must have kicked in: the full
        # unpruned history would be far larger than a small multiple of it.
        self.assertLess(total, 3000 * 4)
        # Most recent turn is kept: the payload ends with the latest tool
        # results, answering the latest assistant tool call.
        self.assertEqual(last_payload[-1]["role"], "tool")
        assistant_msgs = [m for m in last_payload if m.get("role") == "assistant"]
        self.assertTrue(any(m.get("tool_calls") for m in assistant_msgs))

    def test_tool_output_capped_in_payload(self):
        big = "z" * 5000
        (Path(self.tmp.name) / "big.txt").write_text(big)
        script = [
            ("read it", [_tool_call("read_file", {"path": "big.txt"})]),
            ("done", []),
        ]
        loop, transport, ctx, registry = _make_loop(
            self.tmp.name, script, config={"tool_output_limit": 100}
        )
        loop.run("read big")
        tool_msgs = [m for m in transport.payloads[1] if m.get("role") == "tool"]
        self.assertLessEqual(len(tool_msgs[0]["content"]), 200)
        self.assertIn("truncated", tool_msgs[0]["content"])


class TestSkillsAndVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_skills_injected_into_system_prompt(self):
        loop, _, _, _ = _make_loop(
            self.tmp.name,
            [],
            skills=[{"name": "cad", "body": "Always convert CAD first."}],
        )
        prompt = loop.build_system_prompt()
        self.assertIn("## Skill: cad", prompt)
        self.assertIn("Always convert CAD first.", prompt)

    def test_project_instructions_loaded(self):
        (Path(self.tmp.name) / "AGENTS.md").write_text("# Rules\nBe nice.")
        loop, _, _, _ = _make_loop(self.tmp.name, [])
        self.assertIn("Be nice.", loop.build_system_prompt())

    def test_verification_nudge_after_file_change(self):
        script = [
            ("writing", [_tool_call("write_file",
                                   {"path": "out.txt", "content": "data"})]),
            ("all done", []),   # model tries to finish right after writing
            ("verified ok", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("write a file")
        self.assertEqual(result["stopped_reason"], "final_answer")
        nudges = [
            m for m in transport.payloads[2]
            if m.get("role") == "user" and "Before finishing" in m.get("content", "")
        ]
        self.assertEqual(len(nudges), 1)
        self.assertEqual(result["result"], "verified ok")

    def test_no_nudge_without_changes(self):
        script = [("nothing to do", [])]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.run("hi")
        self.assertEqual(result["stopped_reason"], "final_answer")
        self.assertEqual(len(transport.payloads), 1)


class TestChatTurn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "hello.txt").write_text("hello world")

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_kept_across_turns(self):
        script = [("first answer", []), ("second answer", [])]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        r1 = loop.chat_turn("hello")
        self.assertEqual(r1["result"], "first answer")
        r2 = loop.chat_turn("and then?")
        self.assertEqual(r2["result"], "second answer")
        second_payload = transport.payloads[1]
        seen = {(m["role"], m.get("content", "")) for m in second_payload}
        self.assertIn(("user", "hello"), seen)
        self.assertIn(("assistant", "first answer"), seen)
        self.assertIn(("user", "and then?"), seen)

    def test_transport_error_rolls_back_failed_turn(self):
        script = [("ok", []), RuntimeError("boom")]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        loop.chat_turn("first")
        before = len(loop.messages)
        result = loop.chat_turn("second")
        self.assertEqual(result["stopped_reason"], "transport_error")
        self.assertEqual(len(loop.messages), before)
        # Retry after the rollback works.
        retry = loop.chat_turn("second again")
        self.assertEqual(retry["stopped_reason"], "final_answer")

    def test_run_resets_history(self):
        script = [("a1", []), ("a2", [])]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        loop.chat_turn("one")
        self.assertGreater(len(loop.messages), 2)
        result = loop.run("fresh task")
        self.assertEqual(result["result"], "a2")
        # system + new user + assistant only
        self.assertEqual(len(loop.messages), 3)

    def test_reset_clears_history(self):
        script = [("a1", []), ("a2", [])]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        loop.chat_turn("one")
        loop.reset()
        self.assertEqual(loop.messages, [])
        result = loop.chat_turn("two")
        self.assertEqual(result["result"], "a2")
        first_payload = transport.payloads[1]
        self.assertEqual(
            [m["role"] for m in first_payload], ["system", "user"]
        )


    def test_keyboard_interrupt_discards_partial_turn(self):
        script = [("a1", []), KeyboardInterrupt()]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        loop.chat_turn("one")
        before = len(loop.messages)
        with self.assertRaises(KeyboardInterrupt):
            loop.chat_turn("two")
        self.assertEqual(len(loop.messages), before)
        # Session still usable afterwards.
        result = loop.chat_turn("three")
        self.assertEqual(result["stopped_reason"], "final_answer")

    def test_chat_turn_attaches_images(self):
        script = [("I see a cat.", [])]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.chat_turn("what is this?", images=["aGVsbG8="])
        self.assertEqual(result["stopped_reason"], "final_answer")
        user_message = transport.payloads[0][1]
        self.assertEqual(user_message["role"], "user")
        self.assertEqual(user_message["images"], ["aGVsbG8="])

    def test_chat_turn_without_images_has_no_images_key(self):
        script = [("hi", [])]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        loop.chat_turn("hello")
        user_message = transport.payloads[0][1]
        self.assertNotIn("images", user_message)

    def test_tools_unsupported_falls_back_to_tool_less(self):
        script = [
            RuntimeError(
                "Ollama http://127.0.0.1:11434 model='moondream:latest': "
                'HTTP 400 Bad Request {"error": '
                '"registry.ollama.ai/library/moondream:latest '
                'does not support tools"}'
            ),
            ("I see a cat.", []),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.chat_turn("what is this?", images=["aGVsbG8="])
        self.assertEqual(result["stopped_reason"], "final_answer")
        self.assertTrue(result["tools_unavailable"])
        self.assertEqual(result["result"], "I see a cat.")
        # First attempt carried tools; the retry went out tool-less.
        self.assertTrue(transport.tools_seen[0])
        self.assertEqual(transport.tools_seen[1], [])

    def test_tools_unsupported_retry_failure_still_reports(self):
        script = [
            RuntimeError("model='x': HTTP 400 Bad Request does not support tools"),
            RuntimeError("boom"),
        ]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.chat_turn("hi")
        self.assertEqual(result["stopped_reason"], "transport_error")

    def test_unrelated_transport_error_still_fails(self):
        script = [RuntimeError("Ollama: HTTP 500 Internal Server Error")]
        loop, transport, ctx, registry = _make_loop(self.tmp.name, script)
        result = loop.chat_turn("hi")
        self.assertEqual(result["stopped_reason"], "transport_error")
        self.assertNotIn("tools_unavailable", result)


if __name__ == "__main__":
    unittest.main()
