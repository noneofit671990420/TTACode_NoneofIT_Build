# Install TTACode

Two apps ship from this repo; both are built by CI on every `v*` tag
and attached to the same GitHub Release.

| App | File | What it is |
|---|---|---|
| Desktop chat GUI | `ttacode.exe` | Double-click chat app: type, drop in images, the agent runs tools for you (needs no Python install) |
| Headless harness CLI | `ttacode-cli.exe` | Terminal agent for power users: `run --headless`, `chat`, `models`, `mcp`, `skills` |

**Stable shareable links** (always the newest release's files — use these
when linking the builds for others):

- `https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases/latest/download/ttacode.exe`
- `https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases/latest/download/ttacode-cli.exe`

The CI workflow (`.github/workflows/build-exe.yml`) uploads the assets
under exactly these names on every `v*` tag, so the links stay valid
across releases.

Two ways to get the harness CLI. Both end at the same CLI.

## Option A — the .exe (Windows, easiest)

1. Install [Ollama](https://ollama.com) and pull a model, e.g.
   `ollama pull qwen3.5:4b` (small, good default) or a larger one your
   GPU can hold — see [performance](performance.md).
2. Download **`ttacode.exe`** from the
   [Releases page](https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases).

   **Stable shareable link** (always the newest release's exe — use this
   when linking the build for others):
   `https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases/latest/download/ttacode.exe`
   The CI workflow (`.github/workflows/build-exe.yml`) uploads the asset
   under exactly the name `ttacode.exe` on every `v*` tag, so this link
   stays valid across releases.
3. Put it somewhere on your PATH (e.g. `C:\Tools`) or just run it from
   its folder.
4. Open a terminal and run:
   ```
   ttacode.exe init
   ```
   This scans the model stores already on your PC (Ollama, LM Studio),
   picks a sensible default, and writes `%USERPROFILE%\.ttacode\config.json`.
   It never downloads anything.
5. Warm the model into VRAM (optional but recommended — makes the first
   run much faster):
   ```
   ttacode.exe models warm
   ```
6. Run a task:
   ```
   ttacode.exe run --headless "summarize the README in this folder" --project C:\projects\mine
   ```

### SmartScreen / "unknown publisher" note

`ttacode.exe` and `ttacode-cli.exe` are **unsigned preview builds** — the same posture as the
upstream TalkToAi Code preview releases. Windows SmartScreen will likely
show a warning on first run ("Windows protected your PC"). Click *More
info → Run anyway* if you trust the build. The binary is produced by
GitHub Actions from the tagged source on every `v*` tag
(`.github/workflows/build-exe.yml`), so you can audit exactly what went
into it.

## Option B — from Python source (Windows / Linux / macOS)

Requires Python 3.10+.

```bash
git clone https://github.com/noneofit671990420/TTACode_NoneofIT_Build.git
cd TTACode_NoneofIT_Build
git checkout feature/openharness-core   # until this merges to main
python -m harness init
python -m harness run --headless "your task" --project /path/to/project
```

No third-party packages are needed — the harness core is stdlib-only.

## First-run checklist

- `harness init` — scan + config (idempotent, safe to re-run).
- `harness models list` — what this PC has, disk vs live.
- `harness models warm [MODEL]` — pre-load into VRAM.
- `harness mcp list` / `harness mcp check` — MCP server status ([setup](mcp.md)).
- `harness skills list` — discovered skills (`~/.ttacode/skills`, `./skills`).
- `harness run --headless "..."` — the real agent loop.
- `harness ui` — launch the Qt Studio desktop UI from source (needs
  `pip install PySide6==6.8.3`); frozen console builds point at the
  `ttacode.exe` GUI instead. See [studio](studio.md).
- `harness gui` — launch the desktop chat GUI from source (needs
  `pip install PySide6==6.8.3`); this is what `ttacode.exe` ships as.

Config reference: `~/.ttacode/config.json` (all tunables documented in
[performance](performance.md)); MCP servers: `~/.ttacode/mcp.json`
([setup](mcp.md)); custom tools: drop a `.py` plugin in
`~/.ttacode/tools/` ([guide](tool-plugins.md)).
