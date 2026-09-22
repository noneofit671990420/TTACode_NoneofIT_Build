# Harness roadmap — TTACode_NoneofIT_Build

This branch (`feature/openharness-core`) scaffolds the OpenHarness-style
refactor as a new, self-contained `harness/` package. **No existing files
were modified or deleted** (only a section appended to `README.md`).

## What was scaffolded

- `harness/` — stdlib-only package (Python 3.10+), no new dependencies.
  - `cli.py` — `python -m harness` with `init`, `models list|scan`,
    `run --headless`, `mcp list`, `skills list`.
  - `models/scanner.py` — reads on-disk model stores **without** requiring
    Ollama running: Ollama `manifests/…/library/*/*` + `blobs/` size sums,
    and recursive LM Studio store scan. Plus live `/api/tags` inventory
    (same approach as the original `routing.py`) and a documented
    `pick_default()` heuristic (downloaded-only, capability score, smallest
    size wins ties). Defensive: missing dirs / bad JSON never raise.
  - `transports/ollama.py` — thin `urllib` chat transport + `is_reachable`,
    mirroring `app.py::api_chat`.
  - `tools/registry.py` — minimal `ToolRegistry` with `read_file` /
    `list_dir` proof-of-concept tools, bounded to a project root with
    path-escape protection (same idea as `app.py::safe_path`).
  - `mcp/` — **real JSON-RPC 2.0 client** (`harness/mcp/client.py`):
    stdio transport (subprocess, Content-Length framing, stderr drain
    thread, id-matched requests with timeout) and HTTP transport
    (streamable HTTP via urllib — plain JSON and SSE responses — plus
    best-effort legacy SSE). `MCPClient` does the `initialize`
    handshake with protocol-version negotiation, `tools/list` and
    `tools/call` with text-content flattening. `harness/mcp/bridge.py`
    mounts servers into the registry as `mcp__<server>__<tool>`
    (`[mcp:<server>]` description prefix); dead servers warn and are
    skipped, never breaking a run. The bridge is wired into
    `AgentLoop` (optional `mcp_bridge`, `close()`) and the `run` CLI.
    CLI: `mcp list` shows live status + tools, `mcp check` tests each
    server. See `docs/mcp.md` for setup (incl. an npx filesystem-server
    example) and `docs/mcp-roadmap.md` for status. Tests:
    `tests/test_mcp.py` (19 tests: fake stdio server incl. timeout,
    malformed, dead-server and negotiation cases; in-thread HTTP
    server for both response variants; bridge prefixing + skip).
  - `transports/ollama.py` — `OllamaTransport` now carries
    `keep_alive` (default `"30m"` — the biggest latency win on
    consumer GPUs), `num_ctx` and `num_gpu` through to the Ollama
    request, plus `warm()` for pre-loading a model into VRAM.
    CLI: `harness models warm [MODEL]`; `harness init` writes the
    `ollama_keep_alive` / `ollama_num_ctx` / `ollama_num_gpu` tunables
    (backward compatible, never clobbers user values). Rationale and
    the honest verdict on the "new simplex algorithms" question live
    in `docs/performance.md`.
  - Packaging — `ttacode.spec` (PyInstaller single-file console exe,
    Qt explicitly excluded), `build/build-exe.ps1` (Windows one-shot:
    checks Python 3.10+, disposable `.build-venv`, builds, smoke-tests
    `dist\ttacode.exe`), `.github/workflows/build-exe.yml` (builds on
    `windows-latest` for every `v*` tag + manual dispatch, attaches the
    exe to the GitHub Release). `tests/test_packaging.py` guards the
    Qt-free/stdio-only import graph in a fresh interpreter.
    `docs/install.md` is the user-facing install doc (unsigned-preview
    SmartScreen note, same posture as upstream).
  - `skills/` — `SKILL.md` discovery + tiny built-in frontmatter parser
    (no PyYAML needed); example skill at `skills/example-hello/`.
  - `tools/` — full built-in toolset on `ToolRegistry`, all stdlib:
    `harness/tools/builtin/files.py` (read/write/edit/restore with
    checkpoint semantics, list/mkdir/delete, bounded grep,
    sha256-checked writes), `shell.py` (project-rooted commands,
    PowerShell on Windows / `/bin/sh` elsewhere, timeout + tail),
    `web.py` (dependency-free DuckDuckGo search and page fetch).
    `harness/tools/plugins.py` scans `~/.ttacode/tools/*.py` and
    `./tools/*.py` for `register_tools(registry, ctx)`; broken plugins
    warn and are skipped, never crash startup. See
    `docs/tool-plugins.md` for the authoring guide (with a worked
    "CAD to Text" example — illustrative only, not bundled).
  - `agent/loop.py` — `AgentLoop.run()` is real: ports `agent_core.py`'s
    Ollama native tool-calling loop onto `ToolRegistry`, including the
    defensive plain-text/JSON fallback (malformed `<tool_call>` markup
    is never executed; two retries, then an honest stop), the
    edit-checkpoint + verification nudge, and skill + `AGENTS.md`
    injection into the system prompt. Context budgeting: the system
    prompt is always pinned, tool results are capped per message, and
    oldest turns are pruned once the transcript exceeds
    `context_budget_chars`, keeping the most recent
    `history_keep_turns` turns. Generous configurable limits
    (`max_steps` default 50, `tool_output_limit` 12k chars) — no
    artificial token/step ceilings.
  - `cli.py` — `run --headless` is wired end to end:
    `python -m harness run --headless "prompt" [--project PATH]
    [--model NAME] [--max-steps N]`. Resolves the model from
    on-disk stores (never auto-downloads), builds tools + plugins +
    skills, runs the loop, and prints a compact summary.
- `tests/test_model_scanner.py`, `tests/test_skill_loader.py` — stdlib
  `unittest` suites using temp dirs (fake Ollama manifests/blobs, fake
  LM Studio trees).
- `docs/mcp-roadmap.md` — planned stdio/SSE JSON-RPC implementation.

## Dependencies

**None.** The harness core is stdlib-only by design. The existing
`requirements-desktop.txt` (PySide6, etc.) is untouched and only needed
for the legacy Qt studio UI.

## What's next

1. ✅ **Port the agent tool loop** — done (see above). Tests:
   `tests/test_agent_loop.py`, `tests/test_builtin_tools.py`,
   `tests/test_tool_plugins.py` (64 tests total with the scanner/skill
   suites, all passing).
2. ✅ **MCP JSON-RPC** — done: stdio + streamable HTTP (+ legacy SSE
   best-effort), registry bridge, `mcp list`/`mcp check`, 19 tests.
   Deferred: `resources/list` / `prompts/list` surfaced as skills,
   `harness mcp tools <name>`, OAuth-bearing remote servers.
3. ✅ **Skill injection** — active skill bodies are injected into the
   system prompt by `AgentLoop.build_system_prompt()`. Still open:
   `harness skills enable/disable`.
4. ✅ **Single-binary packaging** — done: `ttacode.spec`,
   `build/build-exe.ps1`, CI workflow attaching `ttacode.exe` to
   releases, `docs/install.md`. The authoritative .exe is built on
   Windows CI; a Linux `--onedir` validation build proved the bundle
   works (Linux binary, not a Windows exe).
5. **Qt UI as optional plugin** — slim `studio.py` down to a frontend over
   the harness core instead of owning the agent logic.
6. **Model bootstrap UX** — `harness models pull <name>` as an explicit,
   user-confirmed opt-in (never automatic), plus hardware-aware size
   warnings before large downloads.
7. **Windows installer** — reuse `Setup-Windows.ps1`/`Install.cmd` flow but
   targeting the headless CLI first, GUI optional.
