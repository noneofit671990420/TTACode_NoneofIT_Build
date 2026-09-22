# MCP roadmap

The `harness/mcp` package currently only loads server configuration
(`MCPServerConfig`, `load_servers`). This document describes the planned
JSON-RPC implementation. Nothing here is faked: `MCPClient.connect()`
raises `NotImplementedError` until the work below lands.

## Planned transports

### stdio

- Spawn `config.command + config.args` with `subprocess.Popen`,
  pipes for stdin/stdout, `config.env` merged over the parent environment.
- Frame JSON-RPC 2.0 messages with `Content-Length` headers (LSP-style),
  matching the MCP stdio convention.
- Request ids: incrementing integers, guarded by a lock for threaded use.
- Shutdown: send `shutdown` notification, then SIGTERM with a timeout,
  then SIGKILL. Never leave orphan processes.

### SSE

- `GET {url}` with `Accept: text/event-stream` using stdlib
  `urllib`/`http.client`; parse `event:` / `data:` frames.
- The `endpoint` event gives the message POST URL; JSON-RPC requests go
  there, responses arrive as `message` events correlated by id.
- Reconnect with backoff on dropped streams.

## Planned protocol flow

1. `initialize` with `protocolVersion` negotiation and client capabilities.
2. `notifications/initialized`.
3. `tools/list` → adapt each MCP tool into the harness `ToolRegistry`
   shape (`name`, `description`, JSON schema).
4. `tools/call` → route through `MCPClient.call_tool`, returning the
   MCP result content blocks as a JSON-serializable dict.
5. Optional later: `resources/list`, `prompts/list` surfaced as skills.

## Config

`~/.ttacode/mcp.json` (created by `harness init` when empty):

```json
{"servers": [
  {"name": "filesystem", "command": ["npx", "@modelcontextprotocol/server-filesystem", "/projects"]},
  {"name": "remote-tools", "url": "http://127.0.0.1:9000/sse"}
]}
```

## Security notes

- Stdio servers run with the user's own permissions — same trust model
  as the existing TalkToAi Code shell tools.
- SSE URLs are validated as http(s) only; no credentials are stored in
  the config file (use environment variables via the `env` mapping).
- Tool results from MCP servers are untrusted data: they are quoted
  into prompts, never executed.

## Milestones

- [ ] stdio transport + `initialize` handshake
- [ ] `tools/list` → ToolRegistry adapter
- [ ] `tools/call` end-to-end
- [ ] SSE transport
- [ ] `harness mcp list` shows live server status, `harness mcp tools <name>`
- [ ] Agent loop consumes MCP tools alongside local tools
