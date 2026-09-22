"""Tests for the real MCP client (stdio + HTTP) and the registry bridge.

A fake MCP server is embedded below and spawned over stdio; its behavior
is steered by environment variables so timeout/malformed/dead-server
cases run with no long sleeps. HTTP is tested against a tiny in-thread
``http.server`` speaking streamable HTTP (both plain-JSON and SSE
response variants).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.mcp.bridge import check_server, mcp_tool_name, mount_mcp_tools
from harness.mcp.client import (
    MCPClient,
    MCPError,
    MCPServerConfig,
    _content_to_text,
)
from harness.tools import ToolRegistry, ToolContext

FAKE_SERVER = r'''
import json, os, sys

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline().decode("latin-1")
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    return json.loads(sys.stdin.buffer.read(n))

def write_msg(msg):
    body = json.dumps(msg).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()

mode = os.environ.get("FAKE_MCP_MODE", "ok")
while True:
    msg = read_msg()
    if msg is None:
        break
    mid, method = msg.get("id"), msg.get("method")
    if mode == "hung":
        continue  # never respond -> client must time out
    if mode == "malformed":
        sys.stdout.buffer.write(b"GARBAGE-NOT-JSON\n")
        sys.stdout.buffer.flush()
        continue
    if method == "initialize":
        write_msg({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": os.environ.get("FAKE_MCP_PROTO", "2024-11-05"),
            "capabilities": {},
            "serverInfo": {"name": "fake", "version": "0.0"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        write_msg({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "Echo text",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]}},
            {"name": "weird.name", "description": "Dotted name",
             "inputSchema": {"type": "object", "properties": {}}},
        ]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name == "echo":
            content = [{"type": "text", "text": "echo:" + str(args.get("text", ""))},
                       {"type": "image", "data": "xx", "mimeType": "image/png"}]
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {"content": content}})
        elif name == "boom":
            write_msg({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "kaput"}], "isError": True}})
        else:
            write_msg({"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32602, "message": "unknown tool"}})
    else:
        write_msg({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": "unknown method"}})
'''


class FakeStdioServer:
    """Context manager: writes the fake server script, yields its path."""

    def __init__(self, env: dict | None = None):
        self.env = env or {}
        self._tmpdir = None
        self.path = None
        self._saved = {}

    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fake-mcp-")
        self.path = Path(self._tmpdir.name) / "fake_server.py"
        self.path.write_text(FAKE_SERVER, encoding="utf-8")
        for key, value in self.env.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def config(self, **kw):
        kw.setdefault("command", [sys.executable, str(self.path)])
        return MCPServerConfig(name="fake", **kw)

    def __exit__(self, *_exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmpdir.cleanup()


class TestStdioClient(unittest.TestCase):
    def test_initialize_negotiation(self):
        # Server offers an older protocol version; client must accept it.
        with FakeStdioServer({"FAKE_MCP_PROTO": "2024-11-05"}) as srv:
            with MCPClient(srv.config(), request_timeout=10) as client:
                self.assertEqual(client.protocol_version, "2024-11-05")
                self.assertEqual(client.server_info.get("name"), "fake")

    def test_list_tools_normalized(self):
        with FakeStdioServer() as srv:
            with MCPClient(srv.config(), request_timeout=10) as client:
                tools = client.list_tools()
        by_name = {t["name"]: t for t in tools}
        self.assertIn("echo", by_name)
        self.assertEqual(by_name["echo"]["description"], "Echo text")
        self.assertIn("text", by_name["echo"]["schema"]["properties"])

    def test_call_tool_text_and_nontext(self):
        with FakeStdioServer() as srv:
            with MCPClient(srv.config(), request_timeout=10) as client:
                text = client.call_tool("echo", {"text": "hi"})
        self.assertIn("echo:hi", text)
        self.assertIn("image content omitted", text)

    def test_call_tool_is_error(self):
        with FakeStdioServer() as srv:
            with MCPClient(srv.config(), request_timeout=10) as client:
                with self.assertRaises(MCPError):
                    client.call_tool("boom", {})

    def test_call_unknown_tool_raises(self):
        with FakeStdioServer() as srv:
            with MCPClient(srv.config(), request_timeout=10) as client:
                with self.assertRaises(MCPError) as ctx:
                    client.call_tool("nope", {})
        self.assertIn("unknown tool", str(ctx.exception))

    def test_hung_server_times_out(self):
        with FakeStdioServer({"FAKE_MCP_MODE": "hung"}) as srv:
            client = MCPClient(srv.config(), request_timeout=1.5)
            try:
                with self.assertRaises(MCPError) as ctx:
                    client.connect()  # initialize hangs
            finally:
                client.close()
        self.assertIn("timed out", str(ctx.exception))

    def test_malformed_response_raises(self):
        with FakeStdioServer({"FAKE_MCP_MODE": "malformed"}) as srv:
            client = MCPClient(srv.config(), request_timeout=2)
            try:
                with self.assertRaises(MCPError):
                    client.connect()
            finally:
                client.close()

    def test_dead_command_raises(self):
        cfg = MCPServerConfig(name="dead", command=["/nonexistent/mcp-binary-xyz"])
        with self.assertRaises(MCPError) as ctx:
            MCPClient(cfg, request_timeout=5).connect()
        self.assertIn("not found", str(ctx.exception))

    def test_use_before_connect_raises(self):
        with FakeStdioServer() as srv:
            client = MCPClient(srv.config())
            with self.assertRaises(MCPError):
                client.list_tools()

    def test_content_to_text_blocks(self):
        self.assertEqual(_content_to_text([{"type": "text", "text": "a"}]), "a")
        self.assertIn("omitted", _content_to_text([{"type": "image"}]))
        self.assertEqual(_content_to_text([]), "")
        self.assertEqual(_content_to_text([{"type": "text", "text": "x"},
                                           {"type": "text", "text": "y"}]),
                         "x\ny")


# --------------------------------------------------------------------------
# HTTP (streamable) tests
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    mode = "json"

    def log_message(self, *args):
        pass

    def _send(self, payload, ctype):
        if ctype == "text/event-stream":
            body = ("data: %s\n\n" % json.dumps(payload)).encode()
        else:
            body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n))

    def _dispatch(self, msg):
        mid, method = msg.get("id"), msg.get("method")
        ctype = ("text/event-stream" if self.mode == "sse"
                 else "application/json")
        if method == "initialize":
            return ({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "serverInfo": {"name": "fakehttp", "version": "1.0"}}}, ctype)
        if method == "tools/list":
            return ({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "ping", "description": "Ping",
                 "inputSchema": {"type": "object", "properties": {}}}]}}, ctype)
        if method == "tools/call":
            return ({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "pong"}]}}, ctype)
        return ({"jsonrpc": "2.0", "id": mid,
                 "error": {"code": -32601, "message": "nope"}}, ctype)

    def do_POST(self):
        msg = self._read()
        if msg.get("method") == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        payload, ctype = self._dispatch(msg)
        self._send(payload, ctype)


class _HttpServer:
    def __init__(self, mode):
        _Handler.mode = mode
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}/mcp"

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()


class TestHttpClient(unittest.TestCase):
    def _roundtrip(self, mode):
        with _HttpServer(mode) as url:
            cfg = MCPServerConfig(name="fakehttp", url=url)
            with MCPClient(cfg, request_timeout=10) as client:
                self.assertEqual(client.protocol_version, "2025-06-18")
                tools = client.list_tools()
                self.assertEqual([t["name"] for t in tools], ["ping"])
                self.assertEqual(client.call_tool("ping", {}), "pong")

    def test_streamable_http_json_response(self):
        self._roundtrip("json")

    def test_streamable_http_sse_response(self):
        self._roundtrip("sse")

    def test_unreachable_host(self):
        cfg = MCPServerConfig(name="down", url="http://127.0.0.1:1/mcp")
        with self.assertRaises(MCPError):
            MCPClient(cfg, request_timeout=3).connect()

    def test_non_http_url_rejected(self):
        with self.assertRaises(ValueError):
            MCPServerConfig(name="bad", url="ftp://example.com/x").validate()


# --------------------------------------------------------------------------
# bridge tests
# --------------------------------------------------------------------------

class TestBridge(unittest.TestCase):
    def _registry(self):
        tmp = tempfile.TemporaryDirectory(prefix="mcp-bridge-")
        self.addCleanup(tmp.cleanup)
        return ToolRegistry(), ToolContext(project_root=tmp.name)

    def test_name_sanitizing(self):
        self.assertEqual(mcp_tool_name("fs", "read.file"), "mcp__fs__read_file")
        self.assertEqual(mcp_tool_name("my-srv", "tool"), "mcp__my-srv__tool")

    def test_mount_prefixes_and_calls(self):
        with FakeStdioServer() as srv:
            registry, ctx = self._registry()
            cfg_path = Path(srv.path).parent / "mcp.json"
            cfg_path.write_text(json.dumps({
                "servers": [{"name": "fake",
                             "command": [sys.executable, str(srv.path)]}]
            }), encoding="utf-8")
            bridge = mount_mcp_tools(registry, ctx, config_path=cfg_path,
                                     request_timeout=10)
            try:
                self.assertEqual(bridge.tool_count, 2)
                self.assertEqual(bridge.failed, [])
                names = {t["name"] for t in registry.list_tools()}
                self.assertIn("mcp__fake__echo", names)
                # Dotted MCP tool name got sanitized.
                self.assertIn("mcp__fake__weird_name", names)
                desc = registry.get("mcp__fake__echo")["description"]
                self.assertTrue(desc.startswith("[mcp:fake]"))
                result = registry.call("mcp__fake__echo", {"text": "yo"})
                self.assertIn("echo:yo", result)
            finally:
                bridge.close()

    def test_dead_server_skipped(self):
        with FakeStdioServer() as srv:
            registry, ctx = self._registry()
            cfg_path = Path(srv.path).parent / "mcp.json"
            cfg_path.write_text(json.dumps({"servers": [
                {"name": "dead", "command": ["/nonexistent/mcp-binary-xyz"]},
                {"name": "fake", "command": [sys.executable, str(srv.path)]},
            ]}), encoding="utf-8")
            bridge = mount_mcp_tools(registry, ctx, config_path=cfg_path,
                                     request_timeout=10)
            try:
                self.assertEqual(bridge.tool_count, 2)  # good server still mounted
                self.assertEqual(len(bridge.failed), 1)
                self.assertEqual(bridge.failed[0]["name"], "dead")
            finally:
                bridge.close()

    def test_missing_config_mounts_nothing(self):
        registry, ctx = self._registry()
        bridge = mount_mcp_tools(registry, ctx,
                                 config_path="/nonexistent/mcp.json")
        self.assertEqual(bridge.tool_count, 0)
        self.assertEqual(bridge.failed, [])

    def test_check_server_ok_and_fail(self):
        with FakeStdioServer() as srv:
            ok = check_server(srv.config(), timeout=10)
            self.assertTrue(ok["ok"])
            self.assertEqual(ok["tool_count"], 2)
            self.assertEqual(ok["protocol_version"], "2024-11-05")
        bad = check_server(MCPServerConfig(name="dead",
                                           command=["/nonexistent/mcp-binary-xyz"]),
                           timeout=5)
        self.assertFalse(bad["ok"])
        self.assertIsNotNone(bad["error"])


if __name__ == "__main__":
    unittest.main()
