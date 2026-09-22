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
  - `mcp/` — config loading only (`MCPServerConfig`, `load_servers`);
    `MCPClient.connect()` honestly raises `NotImplementedError`.
    Transport plan lives in `docs/mcp-roadmap.md`.
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
2. **MCP JSON-RPC** — implement stdio transport per `docs/mcp-roadmap.md`,
   then adapt `tools/list` into the registry.
3. ✅ **Skill injection** — active skill bodies are injected into the
   system prompt by `AgentLoop.build_system_prompt()`. Still open:
   `harness skills enable/disable`.
4. **Qt UI as optional plugin** — slim `studio.py` down to a frontend over
   the harness core instead of owning the agent logic.
5. **Single-binary packaging** — PyInstaller one-dir/one-file build of
   just the harness CLI for easy launch (`harness init` → pick model →
   `harness run --headless "…"`).
6. **Model bootstrap UX** — `harness models pull <name>` as an explicit,
   user-confirmed opt-in (never automatic), plus hardware-aware size
   warnings before large downloads.
7. **Windows installer** — reuse `Setup-Windows.ps1`/`Install.cmd` flow but
   targeting the headless CLI first, GUI optional.
