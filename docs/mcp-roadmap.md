# MCP roadmap — status

`harness/mcp` is now a **real JSON-RPC 2.0 client** (`client.py`), not a
skeleton. This document records what shipped and what's deferred.
Setup instructions live in `docs/mcp.md`.

## Shipped

### stdio transport (`StdioTransport`)

- Spawns `config.command + config.args` with `subprocess.Popen`,
  pipes for stdin/stdout/stderr, `config.env` merged over the parent
  environment.
- Frames JSON-RPC 2.0 messages with `Content-Length` headers (the MCP
  stdio convention); defensively also accepts bare single-line JSON.
- Request ids are incrementing integers under a lock; a reader thread
  matches responses by id and `request()` blocks with a configurable
  timeout (default 30 s) — a hung server raises `MCPError`, never hangs.
- Stderr is drained on its own thread (last 20 lines kept) so a chatty
  server can't block the pipes; instant-exit failures report the exit
  code plus stderr tail.
- Shutdown: SIGTERM with a 2 s grace, then SIGKILL; pipes closed. No
  orphan processes (reader threads are daemons).

### HTTP transports

- **Streamable HTTP** (`StreamableHttpTransport`, the current spec):
  POST JSON-RPC with `Accept: application/json, text/event-stream`;
  handles plain-JSON responses and SSE (`text/event-stream`) responses
  (parses `data:` lines, correlates by id).
- **Legacy SSE** (`LegacySseTransport`, best-effort for pre-2025
  servers): GETs the event stream, waits for the `endpoint` event,
  POSTs requests there, correlates responses arriving on the stream.
  `MCPClient` tries streamable HTTP first and falls back to legacy SSE
  on HTTP 404/405.

### Protocol flow (`MCPClient`)

1. `initialize` offering protocolVersion `2025-06-18`, with client
   capabilities + clientInfo — the server's negotiated version is
   accepted whatever it is (older or newer).
2. `notifications/initialized` (best-effort).
3. `tools/list` → normalized `{name, description, schema}` (schema =
   the server's `inputSchema`).
4. `tools/call` → text content blocks concatenated; image/audio and
   embedded resources become honest `[omitted]` placeholders;
   `isError` results raise `MCPError`.
5. Malformed JSON, JSON-RPC error responses, timeouts, dead processes →
   `MCPError` with a clear message, everywhere.

### Bridge (`bridge.py`)

- `mount_mcp_tools(registry, ctx)` reads `~/.ttacode/mcp.json`,
  connects each server, registers tools as `mcp__<server>__<tool>`
  (sanitized to registry name rules) with `[mcp:<server>]` description
  prefix. Dead/unreachable servers warn on stderr and are skipped —
  startup and the agent loop never break.
- Wired into `AgentLoop` (optional `mcp_bridge`, `close()`) and the
  `run --headless` CLI (with a `finally` close and tool-count summary).
- CLI: `harness mcp list` (live per-server status + tool names),
  `harness mcp check` (OK/FAIL per server, non-zero exit on failure).

### Security notes (as before, still true)

- Stdio servers run with the user's own permissions — same trust model
  as the built-in shell tools.
- `url` entries must be http(s); no credentials are stored in the
  config (export tokens in your shell; `env` merges over `os.environ`).
- Tool results from MCP servers are untrusted data: quoted into
  prompts, never executed.

## Deferred

- `resources/list` / `prompts/list` surfaced as skills.
- `harness mcp tools <name>` (per-server tool listing; `mcp list`
  already shows them).
- OAuth / bearer-token flows for hosted remote servers.
- Request cancellation and progress notifications.

## Config

`~/.ttacode/mcp.json` (created empty by `harness init`):

```json
{"servers": [
  {"name": "filesystem", "command": ["npx", "@modelcontextprotocol/server-filesystem", "/projects"]},
  {"name": "remote-tools", "url": "http://127.0.0.1:9000/mcp"}
]}
```

See `docs/mcp.md` for a full worked example.
