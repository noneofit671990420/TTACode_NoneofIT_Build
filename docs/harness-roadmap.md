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
  - `agent/loop.py` — `AgentLoop` skeleton; `run()` raises
    `NotImplementedError` (no faked execution).
- `tests/test_model_scanner.py`, `tests/test_skill_loader.py` — stdlib
  `unittest` suites using temp dirs (fake Ollama manifests/blobs, fake
  LM Studio trees).
- `docs/mcp-roadmap.md` — planned stdio/SSE JSON-RPC implementation.

## Dependencies

**None.** The harness core is stdlib-only by design. The existing
`requirements-desktop.txt` (PySide6, etc.) is untouched and only needed
for the legacy Qt studio UI.

## What's next

1. **Port the agent tool loop** — adapt `agent_core.py`'s Ollama
   tool-calling loop onto `ToolRegistry` + `transports.ollama`, keeping
   the checkpoint/edit semantics. Wire `AgentLoop.run()` for real.
2. **MCP JSON-RPC** — implement stdio transport per `docs/mcp-roadmap.md`,
   then adapt `tools/list` into the registry.
3. **Skill injection** — feed active skill bodies into the system prompt
   (`AgentLoop.build_system_prompt()` already drafts this) and add
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
