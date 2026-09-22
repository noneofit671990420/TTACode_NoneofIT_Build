"""Real MCP (Model Context Protocol) JSON-RPC 2.0 client — stdlib only.

Transports:

* :class:`StdioTransport` — spawns the server subprocess (``command`` +
  ``args``), speaks newline/Content-Length framed JSON-RPC on
  stdin/stdout. Stderr is drained on a background thread so a chatty
  server can never block the pipes.
* :class:`StreamableHttpTransport` — "Streamable HTTP" (the current MCP
  spec): POST JSON-RPC to ``url`` with ``Accept: application/json,
  text/event-stream``; handles plain-JSON responses and SSE
  (``text/event-stream``) responses.
* :class:`LegacySseTransport` — best-effort support for pre-2025 SSE
  servers: GET the event stream, wait for the ``endpoint`` event, POST
  requests there, correlate responses arriving on the stream.

:class:`MCPClient` wraps a transport with the MCP handshake
(``initialize`` with protocol-version negotiation, then
``notifications/initialized``), ``tools/list`` and ``tools/call``.

Everything is defensive: malformed JSON, JSON-RPC error responses,
hung servers and dead processes raise :class:`MCPError` with a clear
message — never a hang, never a traceback leaking to the agent loop.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: Newest MCP protocol version this client offers. Servers negotiate;
#: whatever they answer with is accepted and stored.
LATEST_PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "ttacode-harness"
CLIENT_VERSION = "0.1.0"


class MCPError(Exception):
    """Any MCP transport or protocol failure."""


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

@dataclass
class MCPServerConfig:
    """One MCP server entry."""

    name: str
    command: list[str] = field(default_factory=list)  # stdio transport
    args: list[str] = field(default_factory=list)
    url: str = ""  # HTTP transport (streamable HTTP; legacy SSE auto-detected)
    env: dict[str, str] = field(default_factory=dict)

    @property
    def transport(self) -> str:
        if self.command:
            return "stdio"
        if self.url:
            return "http"
        return "unknown"

    def validate(self) -> None:
        if not self.name:
            raise ValueError("MCP server entry needs a name")
        if not self.command and not self.url:
            raise ValueError(
                f"MCP server {self.name!r} needs 'command' (stdio) or 'url' (HTTP)"
            )
        if self.command and self.url:
            raise ValueError(
                f"MCP server {self.name!r} must use one transport, not both"
            )
        if self.url:
            scheme = urllib.parse.urlsplit(self.url).scheme.lower()
            if scheme not in ("http", "https"):
                raise ValueError(
                    f"MCP server {self.name!r}: url must be http(s), got {self.url!r}"
                )


def load_servers(config_path: str | Path) -> list[MCPServerConfig]:
    """Read MCP server configs from a JSON file.

    Missing file -> empty list (MCP is optional). Malformed JSON or
    invalid entries raise ValueError with a clear message.
    """
    path = Path(config_path).expanduser()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read MCP config {path}: {exc}") from exc
    if isinstance(data, dict):
        entries = data.get("servers", [])
    elif isinstance(data, list):
        entries = data
    else:
        raise ValueError(f"MCP config {path} must be an object or a list")
    servers: list[MCPServerConfig] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid MCP server entry (not an object): {entry!r}")
        server = MCPServerConfig(
            name=str(entry.get("name", "")),
            command=[str(c) for c in entry.get("command", []) or []],
            args=[str(a) for a in entry.get("args", []) or []],
            url=str(entry.get("url", "") or ""),
            env={str(k): str(v) for k, v in (entry.get("env", {}) or {}).items()},
        )
        server.validate()
        servers.append(server)
    return servers


# --------------------------------------------------------------------------
# shared JSON-RPC helpers
# --------------------------------------------------------------------------

def _new_id(counter: list[int], lock: threading.Lock) -> int:
    with lock:
        counter[0] += 1
        return counter[0]


def _check_response(message: dict, method: str) -> dict:
    """Raise MCPError on JSON-RPC error responses; return result dict."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise MCPError(f"Bad JSON-RPC response to {method!r}: {message!r}"[:300])
    if "error" in message and message["error"] is not None:
        err = message["error"]
        detail = err.get("message") if isinstance(err, dict) else err
        code = err.get("code") if isinstance(err, dict) else "?"
        raise MCPError(f"MCP {method!r} failed (code {code}): {detail}")
    result = message.get("result")
    if not isinstance(result, dict):
        raise MCPError(f"MCP {method!r} returned no result object")
    return result


class _BaseTransport:
    """Common id counter for the transports."""

    def __init__(self, request_timeout: float = 30.0) -> None:
        self.request_timeout = request_timeout
        self._id_counter = [0]
        self._id_lock = threading.Lock()

    def _next_id(self) -> int:
        return _new_id(self._id_counter, self._id_lock)

    def open(self) -> None:  # noqa: D102
        raise NotImplementedError

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        raise NotImplementedError

    def notify(self, method: str, params: dict | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:  # noqa: D102
        raise NotImplementedError


# --------------------------------------------------------------------------
# stdio transport
# --------------------------------------------------------------------------

class StdioTransport(_BaseTransport):
    """Subprocess-based JSON-RPC transport (MCP stdio convention).

    Messages are framed with ``Content-Length`` headers (the MCP stdio
    convention); a bare single-line JSON object is also accepted
    defensively for quirky servers. Responses are matched by id on a
    reader thread, so ``request()`` can block with a timeout while
    stderr is drained separately.
    """

    def __init__(self, config: MCPServerConfig, request_timeout: float = 30.0) -> None:
        super().__init__(request_timeout)
        self.config = config
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue = queue.Queue()
        self._stderr_tail: list[str] = []
        self._write_lock = threading.Lock()
        self._closed = False

    # -- lifecycle ------------------------------------------------------
    def open(self) -> None:
        cmd = list(self.config.command) + list(self.config.args)
        env = dict(os.environ)
        env.update(self.config.env)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            raise MCPError(f"MCP server {self.config.name!r}: command not found: {cmd[0]!r}")
        except OSError as exc:
            raise MCPError(f"MCP server {self.config.name!r}: cannot start {cmd[0]!r}: {exc}")
        threading.Thread(target=self._read_loop, name="mcp-stdio-reader",
                         daemon=True).start()
        threading.Thread(target=self._drain_stderr, name="mcp-stdio-stderr",
                         daemon=True).start()
        # Fail fast when the server dies instantly (bad args, missing node…).
        time.sleep(0.15)
        if self._proc.poll() is not None:
            tail = " ".join(self._stderr_tail[-3:]).strip()
            raise MCPError(
                f"MCP server {self.config.name!r} exited immediately "
                f"(code {self._proc.returncode})"
                + (f": {tail}" if tail else "")
            )

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for raw in self._proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            self._stderr_tail.append(line)
            del self._stderr_tail[:-20]

    def _read_exactly(self, n: int) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._proc.stdout.read(remaining)
            if not chunk:
                raise EOFError("stdio EOF")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_loop(self) -> None:
        """Parse framed messages; queue responses, ignore notifications."""
        while not self._closed:
            try:
                message = self._read_message()
            except (EOFError, OSError, ValueError):
                self._responses.put((None, {"__eof__": True}))
                return
            if message is None:
                continue
            mid = message.get("id")
            if mid is None:
                continue  # notification from server — nothing to correlate
            self._responses.put((mid, message))

    def _read_message(self) -> dict | None:
        assert self._proc is not None and self._proc.stdout is not None
        out = self._proc.stdout
        # Peek the first line: headers (Content-Length) or a bare JSON line.
        first = out.readline()
        if not first:
            raise EOFError("stdio EOF")
        stripped = first.strip()
        headers: dict[str, str] = {}
        if stripped and not stripped.startswith((b"{", b"[")):
            # Header block: "Content-Length: 123" lines until a blank line.
            line = first
            while line.strip():
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
                line = out.readline()
                if not line:
                    raise EOFError("stdio EOF")
            length = int(headers.get("content-length", "0") or 0)
            if length <= 0 or length > 64 * 1024 * 1024:
                return None  # absurd length — skip defensively
            body = self._read_exactly(length)
        else:
            body = stripped
        try:
            message = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None  # malformed line — skip, keep reading
        return message if isinstance(message, dict) else None

    # -- protocol -------------------------------------------------------
    def _write_frame(self, payload: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        if self._proc.poll() is not None:
            raise MCPError(
                f"MCP server {self.config.name!r} exited "
                f"(code {self._proc.returncode})"
            )
        body = json.dumps(payload).encode("utf-8")
        frame = b"Content-Length: %d\r\n\r\n" % len(body) + body
        with self._write_lock:
            try:
                self._proc.stdin.write(frame)
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise MCPError(f"MCP server {self.config.name!r}: write failed: {exc}")

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        rid = self._next_id()
        payload: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        self._write_frame(payload)
        limit = self.request_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"MCP server {self.config.name!r}: request {method!r} "
                    f"timed out after {limit:g}s"
                )
            try:
                got_id, message = self._responses.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(
                    f"MCP server {self.config.name!r}: request {method!r} "
                    f"timed out after {limit:g}s"
                )
            if message.get("__eof__"):
                raise MCPError(
                    f"MCP server {self.config.name!r} closed its stdout "
                    f"during request {method!r}"
                )
            if got_id == rid:
                return message
            # Not ours (shouldn't happen — ids are unique — but never lose it).
            self._responses.put((got_id, message))

    def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._write_frame(payload)
        except MCPError:
            pass  # notifications are best-effort

    def close(self) -> None:
        self._closed = True
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


# --------------------------------------------------------------------------
# HTTP transports
# --------------------------------------------------------------------------

def _http_post(url: str, payload: dict, timeout: float,
               accept: str) -> tuple[int, dict[str, str], bytes]:
    """POST JSON; return (status, headers, body)."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": accept,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        raise MCPError(f"HTTP {exc.code} from {url}: {exc.read(500)!r}"[:200])
    except (OSError, ValueError) as exc:
        raise MCPError(f"HTTP request to {url} failed: {exc}")


def _parse_sse_messages(raw: bytes) -> list[dict]:
    """Extract JSON-RPC messages from SSE ``data:`` lines."""
    messages: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            message = json.loads(data)
        except ValueError:
            continue
        if isinstance(message, dict):
            messages.append(message)
    return messages


class StreamableHttpTransport(_BaseTransport):
    """MCP "Streamable HTTP": POST JSON-RPC, read JSON or SSE responses."""

    def __init__(self, config: MCPServerConfig, request_timeout: float = 30.0) -> None:
        super().__init__(request_timeout)
        self.url = config.url
        self.name = config.name

    def open(self) -> None:
        pass  # stateless; initialize() fails fast on unreachable hosts

    def _post(self, payload: dict, timeout: float) -> dict | None:
        status, headers, body = _http_post(
            self.url, payload, timeout, "application/json, text/event-stream"
        )
        if status not in (200, 201, 202):
            raise MCPError(f"MCP server {self.name!r}: HTTP {status}")
        ctype = headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            messages = _parse_sse_messages(body)
            rid = payload.get("id")
            for message in messages:
                if message.get("id") == rid:
                    return message
            return None  # notification-style response: nothing to correlate
        if not body.strip():
            return None
        try:
            message = json.loads(body.decode("utf-8"))
        except ValueError:
            raise MCPError(f"MCP server {self.name!r}: non-JSON HTTP response")
        return message if isinstance(message, dict) else None

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        rid = self._next_id()
        payload: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        limit = self.request_timeout if timeout is None else timeout
        message = self._post(payload, limit)
        if message is None:
            raise MCPError(
                f"MCP server {self.name!r}: no JSON-RPC response to {method!r}"
            )
        return message

    def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload, self.request_timeout)
        except MCPError:
            pass  # notifications are best-effort

    def close(self) -> None:
        pass


class LegacySseTransport(_BaseTransport):
    """Best-effort support for pre-2025 SSE-only MCP servers.

    GETs the event stream, waits for the ``endpoint`` event, POSTs
    JSON-RPC requests to that endpoint URL, and correlates responses
    arriving back on the stream by id. Slower to set up than streamable
    HTTP and kept only for old servers — prefer streamable HTTP.
    """

    def __init__(self, config: MCPServerConfig, request_timeout: float = 30.0) -> None:
        super().__init__(request_timeout)
        self.url = config.url
        self.name = config.name
        self._events: queue.Queue = queue.Queue()
        self._endpoint: str | None = None
        self._stream = None
        self._closed = False

    def open(self) -> None:
        request = urllib.request.Request(
            self.url, headers={"Accept": "text/event-stream"}, method="GET"
        )
        try:
            self._stream = urllib.request.urlopen(request, timeout=self.request_timeout)
        except (OSError, ValueError) as exc:
            raise MCPError(f"MCP server {self.name!r}: SSE stream failed: {exc}")
        threading.Thread(target=self._read_stream, name="mcp-sse-reader",
                         daemon=True).start()
        deadline = time.monotonic() + self.request_timeout
        while self._endpoint is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise MCPError(
                    f"MCP server {self.name!r}: no 'endpoint' event on SSE stream"
                )
            try:
                event, data = self._events.get(timeout=remaining)
            except queue.Empty:
                self.close()
                raise MCPError(
                    f"MCP server {self.name!r}: no 'endpoint' event on SSE stream"
                )
            if event == "endpoint" and data:
                # Re-queue anything else; endpoint arrives first in practice.
                self._endpoint = urllib.parse.urljoin(self.url, data.strip())
            else:
                self._events.put((event, data))

    def _read_stream(self) -> None:
        assert self._stream is not None
        event = ""
        try:
            for raw in self._stream:
                if self._closed:
                    return
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    self._events.put((event, line[5:].strip()))
                    event = ""
                elif not line:
                    event = ""
        except (OSError, ValueError):
            pass

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> dict:
        if not self._endpoint:
            raise MCPError(f"MCP server {self.name!r}: SSE endpoint not established")
        rid = self._next_id()
        payload: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        limit = self.request_timeout if timeout is None else timeout
        # Drain stale events first so an old message can't shadow the reply.
        _http_post(self._endpoint, payload, limit, "application/json")
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"MCP server {self.name!r}: request {method!r} "
                    f"timed out after {limit:g}s"
                )
            try:
                _event, data = self._events.get(timeout=remaining)
            except queue.Empty:
                raise MCPError(
                    f"MCP server {self.name!r}: request {method!r} "
                    f"timed out after {limit:g}s"
                )
            if not data or data == "[DONE]":
                continue
            try:
                message = json.loads(data)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == rid:
                return message

    def notify(self, method: str, params: dict | None = None) -> None:
        if not self._endpoint:
            return
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            _http_post(self._endpoint, payload, self.request_timeout,
                       "application/json")
        except MCPError:
            pass

    def close(self) -> None:
        self._closed = True
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

def _content_to_text(content: list) -> str:
    """Flatten MCP content blocks to plain text for the model."""
    parts: list[str] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text", "")))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} content omitted — not representable as text]")
        elif kind == "resource":
            resource = block.get("resource", {})
            text = resource.get("text") if isinstance(resource, dict) else None
            parts.append(str(text) if text else "[embedded resource omitted]")
        else:
            parts.append(json.dumps(block, ensure_ascii=False)[:2000])
    return "\n".join(p for p in parts if p)


class MCPClient:
    """JSON-RPC 2.0 client for one MCP server (stdio or HTTP)."""

    def __init__(self, config: MCPServerConfig, request_timeout: float = 30.0) -> None:
        config.validate()
        self.config = config
        self.request_timeout = request_timeout
        self.transport: _BaseTransport | None = None
        self.protocol_version: str | None = None
        self.server_info: dict = {}

    # -- connection -----------------------------------------------------
    def _build_http_transport(self) -> _BaseTransport:
        return StreamableHttpTransport(self.config, self.request_timeout)

    def connect(self) -> "MCPClient":
        """Open the transport and run the MCP handshake. Returns self."""
        if self.config.command:
            transport: _BaseTransport = StdioTransport(
                self.config, self.request_timeout
            )
            transport.open()
            self.transport = transport
        else:
            # Prefer streamable HTTP; fall back to legacy SSE on 404/405.
            transport = self._build_http_transport()
            try:
                self._handshake(transport)
            except MCPError as exc:
                if "HTTP 404" in str(exc) or "HTTP 405" in str(exc):
                    legacy = LegacySseTransport(self.config, self.request_timeout)
                    legacy.open()
                    self._handshake(legacy)
                    self.transport = legacy
                    return self
                raise
            self.transport = transport
            return self
        self._handshake(self.transport)
        return self

    def _handshake(self, transport: _BaseTransport) -> None:
        message = transport.request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        result = _check_response(message, "initialize")
        # Accept whatever the server negotiates (older or newer).
        self.protocol_version = str(
            result.get("protocolVersion", LATEST_PROTOCOL_VERSION)
        )
        info = result.get("serverInfo")
        self.server_info = dict(info) if isinstance(info, dict) else {}
        transport.notify("notifications/initialized")

    # -- tools ----------------------------------------------------------
    def list_tools(self) -> list[dict]:
        """Normalized tools: ``{name, description, schema}``.

        ``schema`` is the server's ``inputSchema`` (a JSON-Schema object);
        the bridge wraps it into the harness function-tool shape.
        """
        self._require_open()
        assert self.transport is not None
        message = self.transport.request("tools/list")
        result = _check_response(message, "tools/list")
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise MCPError("MCP tools/list returned a non-list 'tools' value")
        tools: list[dict] = []
        for entry in raw_tools:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            schema = entry.get("inputSchema") or {"type": "object"}
            if not isinstance(schema, dict):
                schema = {"type": "object"}
            tools.append(
                {
                    "name": str(entry["name"]),
                    "description": str(entry.get("description", "")),
                    "schema": schema,
                }
            )
        return tools

    def call_tool(self, name: str, arguments: dict | None) -> str:
        """Call a tool; returns flattened text. Raises MCPError on failure."""
        self._require_open()
        assert self.transport is not None
        message = self.transport.request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        result = _check_response(message, "tools/call")
        if result.get("isError"):
            detail = _content_to_text(result.get("content", []))
            raise MCPError(f"MCP tool {name!r} reported an error: {detail}"[:2000])
        return _content_to_text(result.get("content", []))

    # -- lifecycle ------------------------------------------------------
    def _require_open(self) -> None:
        if self.transport is None:
            raise MCPError("MCP client is not connected — call connect() first")

    def close(self) -> None:
        transport, self.transport = self.transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    def __enter__(self) -> "MCPClient":
        return self.connect()

    def __exit__(self, *_exc) -> None:
        self.close()
