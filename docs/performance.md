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

Both cards have 10–12 GB VRAM. The whole game is: **fit the model fully
in VRAM, keep it warm, and don't re-send the world every turn.**

1. **Right-size the model.** A 7–14B model at Q4_K_M quantization
   (~4.5–9 GB) fits comfortably with room for context. A 27–30B model
   (~17–19 GB) will spill to system RAM on these cards and feel 3–10×
   slower. `harness models list` shows sizes; `harness init` prefers
   small downloaded models for exactly this reason.
2. **Full GPU offload.** Make sure Ollama loads all layers on the GPU
   (`ollama ps` shows `100% GPU`). Partial offload = slow.
3. **Keep the model warm.** Cold loads take tens of seconds; warm
   inference answers in seconds. The harness sets Ollama `keep_alive`
   (default `30m`, configurable via `ollama_keep_alive` in
   `~/.ttacode/config.json`) on every request, and `harness models warm`
   pre-loads the model into VRAM before your first real run.
4. **Sane context size.** `ollama_num_ctx` defaults to 8192. Larger
   contexts cost VRAM and attention time; the harness's char-based
   context budgeting (`context_budget_chars`, `history_keep_turns`)
   keeps prompts bounded so you don't pay for stale history.
5. **One model at a time.** Loading two models evicts VRAM and forces
   reloads. The harness talks to one model per run.

## What the harness does for latency

- `keep_alive` on every Ollama request (no cold-start between turns).
- `models warm` pre-loads VRAM before a session.
- Non-streaming single-shot turns with bounded context — fewer, fuller
  requests instead of many small ones.
- Tool results capped (`tool_output_limit`) and old turns pruned, so
  prompt size — the main per-token cost driver — stays flat over long
  sessions.
- Localhost HTTP to Ollama; no cloud round-trips, ever.

## Tuning knobs (`~/.ttacode/config.json`)

| Key | Default | Effect |
|---|---|---|
| `ollama_keep_alive` | `"30m"` | How long Ollama keeps the model in VRAM (`"24h"`, `"-1"` = forever). Longer = always warm, but VRAM stays reserved. |
| `ollama_num_ctx` | `8192` | Context window. Raise for big codebases if VRAM allows. |
| `ollama_num_gpu` | `0` | `0` = Ollama's default auto-offload; set high (e.g. `999`) to force full GPU offload. |
| `max_steps` | `50` | Agent step budget — a safety rail, not a paywall. |
| `context_budget_chars` | `100000` | Approx. prompt budget before old turns are pruned. |
| `history_keep_turns` | `20` | Recent turns always kept, so tool chains never orphan. |
| `tool_output_limit` | `12000` | Per-tool-result cap with a `…[truncated]` marker. |

Change a value, re-run — no rebuild needed (`harness init` never
clobbers values you've customized).
