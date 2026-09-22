# MCP servers in the TTACode harness

The harness speaks **Model Context Protocol (JSON-RPC 2.0)** to external
tool servers and mounts each server's tools into the agent's tool
registry as `mcp__<server>__<tool>`. Transports are stdlib-only:

- **stdio** — the harness spawns `command + args` and talks JSON-RPC
  over stdin/stdout (Content-Length framing, the MCP stdio convention).
- **HTTP** — "Streamable HTTP": POST JSON-RPC to `url`, reading plain
  JSON or SSE responses. Pre-2025 SSE-only servers are supported on a
  best-effort basis (GET the event stream, `endpoint` event, POST there).

Configuration lives at `~/.ttacode/mcp.json`
(`%USERPROFILE%\.ttacode\mcp.json` on Windows):

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "C:\\projects"],
      "args": [],
      "env": {}
    },
    {
      "name": "remote-tools",
      "url": "http://127.0.0.1:9000/mcp"
    }
  ]
}
```

## Real-world example: filesystem server via npx

The official `@modelcontextprotocol/server-filesystem` package exposes
directory listing, file read/write and search scoped to the directories
you pass on its command line.

1. Install Node.js 18+ (LTS) from https://nodejs.org — **npx/node are
   your responsibility**; the harness never installs them.
2. Verify it runs once by hand:
   `npx -y @modelcontextprotocol/server-filesystem C:\projects`
   (it should sit waiting on stdin — Ctrl+C to stop).
3. Add the server entry above to `~/.ttacode/mcp.json`, pointing at the
   directories you want the agent to see.
4. `ttacode.exe mcp check` (or `python -m harness mcp check`) — expect
   `OK filesystem: ... N tools`.
5. `ttacode.exe run --headless "list the files in my project"` — the
   agent now sees `mcp__filesystem__list_directory` etc. alongside the
   built-in tools.

On Linux/macOS the same entry works with a POSIX path, e.g.
`"/home/you/projects"`.

## Commands

- `harness mcp list` — configured servers with **live** status: OK plus
  tool names, or an honest `unreachable: <reason>`.
- `harness mcp check` — same checks, exits non-zero if any server fails.
  Useful in scripts and CI.

## Behavior notes

- **Dead servers never break a run.** If a server can't be started or
  doesn't answer, it is skipped with a stderr warning and the agent
  continues with the remaining tools.
- **Tool results are untrusted data.** MCP output is quoted into the
  model context; it is never executed. A malicious or buggy server can
  at worst confuse the model, not run code outside its own sandbox.
- **Stdio servers run as you.** They inherit your user permissions —
  same trust model as the built-in `run_command` tool. Only configure
  servers you trust, and scope filesystem servers to directories you're
  comfortable exposing.
- **No secrets in mcp.json.** The per-server `env` mapping merges *over*
  the parent environment, so the cleanest pattern is to export tokens
  in your shell (e.g. `setx GITHUB_TOKEN ...` / `export GITHUB_TOKEN=...`)
  and let the server inherit them — no `env` entry needed at all. Only
  put a literal secret in `env` if you're comfortable storing it in a
  plaintext config file.
- **Timeouts:** 30 s per request by default. A hung server raises a
  clear error instead of stalling the agent loop.

## Writing your own server

Any program that speaks MCP JSON-RPC over stdio works — see the
[Model Context Protocol specification](https://modelcontextprotocol.io)
and the fake server in `tests/test_mcp.py` for the minimal message
shapes (`initialize` → `notifications/initialized` → `tools/list` →
`tools/call`).
