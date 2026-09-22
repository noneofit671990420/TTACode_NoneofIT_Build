# Performance: making local runs feel zippy

## The simplex question — honest verdict

Recent headlines describe a genuine breakthrough in the **simplex method
for linear programming**: Bach & Huiberts proved an optimal smoothed
complexity bound for simplex pivoting (arXiv:2504.04197, presented at
FOCS 2025), settling how fast simplex can be in a precise theoretical
sense.

It does **not** apply to LLM inference. Simplex solves linear programs
by walking polyhedron vertices; a transformer generates tokens by
matrix multiplication on a GPU. They share no computational machinery,
so there is nothing to port from the new simplex analysis into model
serving or tool calling. If you want faster local runs, the wins below
are the real ones — they're what this harness actually implements.

## What actually makes it feel zippy on a 3080 / 4070

Desktop 3080 / 4070 cards have 10–12 GB VRAM. The whole game is:
**fit the model fully in VRAM, keep it warm, and don't re-send the
world every turn.**

1. **Right-size the model.** A 7–14B model at Q4_K_M quantization
   (~4.5–9 GB) fits comfortably with room for context. A 27–30B model
   (~17–19 GB) will spill to system RAM on these cards and feel 3–10×
   slower. `harness models list` shows sizes and a FIT marker against
   your VRAM budget; `harness init` prefers small downloaded models for
   exactly this reason.
2. **Full GPU offload.** Make sure Ollama loads all layers on the GPU
   (`ollama ps` shows `100% GPU`). Partial offload = slow. The harness
   sets `ollama_num_gpu=-1` (offload everything the GPU can hold) by
   default.
3. **Keep the model warm.** Cold loads take tens of seconds; warm
   inference answers in seconds. The harness warms the model into VRAM
   automatically at load time (`warm_on_load`, disable with
   `--no-warm`) and sets Ollama `keep_alive` (default `30m`,
   configurable via `ollama_keep_alive` in `~/.ttacode/config.json`) on
   every request, so the model stays resident between runs.
4. **Sane context size.** `ollama_num_ctx` defaults to 8192. Larger
   contexts cost VRAM and attention time; the harness's char-based
   context budgeting (`context_budget_chars`, `history_keep_turns`)
   keeps prompts bounded so you don't pay for stale history.
5. **One model at a time.** Loading two models evicts VRAM and forces
   reloads. The harness talks to one model per run.

## 8 GB laptop GPUs (e.g. RTX 4070 Laptop)

A laptop 4070 has **8 GB VRAM**, and in practice less than that is
usable: the display server, the OS, and the KV cache / context all need
their share. The harness budgets **90% of detected VRAM for weights**
— about **7.2 GiB** on an 8 GB card — and prefers models that fit
inside it (`harness models list` shows `✓` / `!` / `?` fit markers).

What this means in practice (estimates, not benchmarks):

- **Comfort zone:** 7–8B dense models at Q4_K_M, roughly 4.5–5.5 GiB
  (e.g. a 7B-class coder/instruct). Fast, fully resident, room for
  context.
- **Borderline:** 13–14B at Q3 (roughly 6–7 GiB). Fits the weight
  budget but leaves little room for long contexts — fine for short
  tasks, watch for slowdowns on big codebases.
- **Too big:** 14B at Q4 (~9 GiB) and anything larger will spill to
  system RAM.

**Why MoE active-params matter here:** in a mixture-of-experts model
only a few billion "active" parameters run per token, so an MoE can
feel much faster than a dense model of the same total size — *but the
full weight file still has to fit in VRAM*. A 30B-A3B MoE at Q3 is
~12 GiB on disk: quick per token, but it won't fit an 8 GB card. The
harness budgets on total size (what must be resident), which is the
honest number.

**Reading the fit markers** (`harness models list`):

- `✓` — estimated size fits the 90% VRAM budget. Good default.
- `!` — exceeds the budget. It will still run, but expect layers on
  CPU and noticeably slower tokens.
- `?` — size unknown (e.g. live-only model). Try it and watch
  `ollama ps`.

**What CPU spill feels like:** the first tokens take a long time,
generation stutters, and `ollama ps` shows less than `100% GPU`.
Fix: pick a smaller quant (`ollama pull <model>:q4_k_m` vs a larger
one), or lower `ollama_num_ctx` to free VRAM for weights.

`harness init` auto-detects VRAM via `nvidia-smi` and stores it as
`vram_gb` in the config. If detection fails it assumes 8.0 — check the
value if your card differs.

## What the harness does for latency

- **Tuning is part of model load** (`harness/models/loader.py`): every
  run resolves the model, applies `ollama_num_gpu` / `ollama_num_ctx` /
  `ollama_keep_alive`, and warms it into VRAM before the agent loop
  starts. No separate manual step; `--no-warm` skips the warm-up.
- `keep_alive` on every Ollama request (no cold-start between turns).
- `models warm` pre-loads VRAM before a session (manual version of the
  automatic load-time warm).
- VRAM-aware default pick: `harness init` detects VRAM via `nvidia-smi`
  and prefers downloaded models fitting 90% of it.
- Non-streaming single-shot turns with bounded context — fewer, fuller
  requests instead of many small ones.
- Tool results capped (`tool_output_limit`) and old turns pruned, so
  prompt size — the main per-token cost driver — stays flat over long
  sessions.
- Localhost HTTP to Ollama; no cloud round-trips, ever.

## Tuning knobs (`~/.ttacode/config.json`)

| Key | Default | Effect |
|---|---|---|
| `vram_gb` | auto-detected (8.0 fallback) | VRAM budget basis. Detected via `nvidia-smi` at `init`; set manually if wrong. |
| `warm_on_load` | `true` | Warm the model into VRAM automatically on every load. `--no-warm` overrides per run. |
| `ollama_keep_alive` | `"30m"` | How long Ollama keeps the model in VRAM (`"24h"`, `"-1"` = forever). Longer = always warm, but VRAM stays reserved. |
| `ollama_num_ctx` | `8192` | Context window. Raise for big codebases if VRAM allows. |
| `ollama_num_gpu` | `-1` | `-1` = offload as many layers as the GPU can hold (Ollama's own default); `0` disables GPU offload. |
| `max_steps` | `50` | Agent step budget — a safety rail, not a paywall. |
| `context_budget_chars` | `100000` | Approx. prompt budget before old turns are pruned. |
| `history_keep_turns` | `20` | Recent turns always kept, so tool chains never orphan. |
| `tool_output_limit` | `12000` | Per-tool-result cap with a `…[truncated]` marker. |

Change a value, re-run — no rebuild needed (`harness init` never
clobbers values you've customized).
