# TalkToAi Code Studio (Qt desktop UI)

The Studio is the desktop conversation-and-agent workspace (`studio.py`,
PySide6). It now uses the headless harness for everything model-related:
the same discovery, VRAM-aware picks, tuning, and warm-up as the CLI —
one model-load path for the whole project.

## Launching it

**From source** (repo root, Python 3.10+, Qt installed):

```bash
pip install PySide6==6.8.3        # or: pip install -r requirements-desktop.txt
python -m harness ui
```

**As a download** (Windows): grab **`ttacode-studio.exe`** from the
[Releases page](https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases).

Stable shareable link (always the newest release's exe):
`https://github.com/noneofit671990420/TTACode_NoneofIT_Build/releases/latest/download/ttacode-studio.exe`

The console `ttacode.exe` does **not** bundle the UI: running
`ttacode.exe ui` prints the link above and exits. The two exes are built
by separate jobs in `.github/workflows/build-exe.yml` and attached to
the same GitHub Release on every `v*` tag.

## What the harness bridge changes

All of this lives in `harness/ui_bridge.py` (stdlib-only, no Qt):

- **VRAM-aware model picker.** "Use installed local model…" now lists
  only models the Ollama transport can actually serve, with FIT markers
  against your configured `vram_gb` budget: `✓` fits VRAM, `!` exceeds
  the budget (expect CPU spill), `?` size unknown. Fits sort first.
- **Resolve → tune → warm at send time.** When you send a task on a
  local route, Studio calls `prepare_local_model()`: the model is
  verified on this PC, tuned (`ollama_num_gpu` / `ollama_num_ctx` /
  `ollama_keep_alive` from `~/.ttacode/config.json`), and warmed into
  VRAM before the agent runs. Same behavior as `run --headless`.
- **No auto-downloads, ever.** The old Studio would `ollama pull` a
  missing model on send. The harness never downloads: if the requested
  model isn't there (or is a bare LM Studio GGUF Ollama can't serve),
  you get an honest error containing the exact fix — e.g.
  ``ollama pull qwen2.5-coder:7b`` or the `ollama create` Modelfile
  recipe — and nothing happens until you run it yourself.

Studio reads its harness settings (VRAM budget, tunables) from
`~/.ttacode/config.json` — the same file `ttacode.exe init` writes. Run
`init` once and both the CLI and the UI agree on the model.

## Qt dependency note

`PySide6==6.8.3` is pinned in `requirements-desktop.txt` /
`requirements-runtime.txt`. The harness core (`harness/`) never imports
it — the packaging tests enforce that — so the headless CLI stays
dependency-free. Only `studio.py` (and the `ttacode-studio.exe` build)
needs Qt.

## SmartScreen / "unknown publisher" note

Like `ttacode.exe`, `ttacode-studio.exe` is an **unsigned preview
build**. Windows SmartScreen will likely warn on first launch — *More
info → Run anyway* if you trust the build. Both exes are produced by
GitHub Actions from the tagged source (`.github/workflows/build-exe.yml`),
so you can audit exactly what went into them.
