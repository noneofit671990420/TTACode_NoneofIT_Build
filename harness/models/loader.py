"""The single model-load path: resolve → tune → warm → run.

Every CLI command that needs a model goes through :func:`load_model`,
so tuning (``num_gpu`` / ``num_ctx`` / ``keep_alive``) and the VRAM
warm-up happen automatically at load time — never as a separate manual
step the user has to remember.

VRAM awareness: :func:`detect_vram_gb` probes ``nvidia-smi`` (best
effort, never raises); the configured ``vram_gb`` is used to budget
model picks so the harness prefers models that fit fully in VRAM.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from .scanner import discover_all, discover_live_models, pick_default
from ..transports.ollama import OllamaTransport, is_reachable

_GIB = 1024**3
# Fraction of VRAM budgeted for model weights; the rest is headroom for
# the KV cache / context, the display server, and OS overhead.
_VRAM_HEADROOM = 0.9


class ModelLoadError(Exception):
    """Raised when a model cannot be resolved or reached. Honest, no faking."""


def detect_vram_gb() -> float | None:
    """Best-effort total VRAM of the first NVIDIA GPU, in GiB.

    Runs ``nvidia-smi --query-gpu=memory.total`` and converts MiB to GiB.
    Returns ``None`` on any failure (no NVIDIA GPU, no driver,
    ``nvidia-smi`` missing, unparsable output). Never raises — a scanner
    must never crash first-run setup on a machine it has never seen.
    Works on Windows and Linux.
    """
    try:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        proc = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        lines = proc.stdout.strip().splitlines()
        if not lines:
            return None
        mib = float(lines[0].strip().split()[0])
        if mib <= 0:
            return None
        return round(mib / 1024, 1)
    except Exception:
        return None


def vram_budget_bytes(vram_gb: float | None) -> int | None:
    """Weight budget in bytes (90% of VRAM), or None when VRAM unknown."""
    if vram_gb is None or vram_gb <= 0:
        return None
    return int(vram_gb * _VRAM_HEADROOM * _GIB)


def resolve_model_url(
    model: str, ports: tuple[int, ...] = (11434, 11435)
) -> str | None:
    """Pick a reachable Ollama endpoint that serves ``model``.

    Prefers a server whose /api/tags actually lists the model (or its
    ``:latest`` variant); falls back to the first reachable server;
    ``None`` when nothing answers.
    """
    live = discover_live_models(ports)
    for port in ports:
        names = live.get(port, [])
        if model in names or model + ":latest" in names or model.split(":")[0] in names:
            return f"http://127.0.0.1:{port}"
    for port in ports:
        if is_reachable(f"http://127.0.0.1:{port}"):
            return f"http://127.0.0.1:{port}"
    return None


def _match_record(models: list[dict], name: str) -> dict | None:
    """Find a discovery record for ``name`` (exact, or base-name match)."""
    for record in models:
        if record.get("name") == name:
            return record
    base = name.split(":")[0]
    for record in models:
        if record.get("name") == base or record.get("name", "").split(":")[0] == base:
            return record
    return None


@dataclass
class LoadedModel:
    """A model that has been resolved, tuned, and (optionally) warmed."""

    name: str
    url: str
    transport: OllamaTransport
    options: dict = field(default_factory=dict)
    size_bytes: int | None = None
    vram_gb: float | None = None
    fits_vram: bool | None = None  # None = unknown (size or VRAM unknown)
    warmed: bool = False


def load_model(
    name: str | None = None,
    config: dict | None = None,
    *,
    ports: tuple[int, ...] | None = None,
    warm: bool | None = None,
    out=None,
) -> LoadedModel:
    """Resolve, tune, and warm a model — the one load path for the CLI.

    * ``name`` — explicit model name; verified against discovery (honest
      error when the model isn't on this PC). ``None`` → auto-pick via
      :func:`pick_default` with the configured ``vram_gb`` budget.
    * ``config`` — harness config dict (tunables read with ``.get`` so
      old configs without the new keys keep working).
    * ``warm`` — pre-load into VRAM; ``None`` → config ``warm_on_load``
      (default ``True``). Pass ``False`` (``--no-warm``) to skip.
    * ``out`` — print-like callable for the one-line summary
      (defaults to :func:`print`; pass a no-op in tests).

    Tuning applied from config: ``ollama_num_gpu`` (``-1`` = full GPU
    offload), ``ollama_num_ctx``, ``ollama_keep_alive``.

    Raises :class:`ModelLoadError` when no model can be resolved or no
    Ollama server answers. A failed warm-up is a warning, not fatal —
    the subsequent run surfaces real transport errors honestly.
    """
    say = out if out is not None else print
    config = dict(config or {})
    if ports is None:
        ports = tuple(
            config.get("model_sources", {}).get("ollama_ports", [11434, 11435])
        ) or (11434, 11435)
    vram_gb = config.get("vram_gb")

    models = discover_all(ports=ports)
    if name:
        record = _match_record(models, name)
        if record is None:
            known = ", ".join(sorted(m["name"] for m in models[:8]))
            hint = f" Known models: {known}…" if known else ""
            raise ModelLoadError(
                f"Model {name!r} is not on this PC.{hint} "
                "Pull it with `ollama pull <name>` or run `harness models list`."
            )
    else:
        record = pick_default(models, vram_gb=vram_gb)
        if record is None:
            raise ModelLoadError(
                "No downloaded models found. Run `harness init` for guidance — "
                "it never downloads anything itself."
            )
    model_name = record["name"]

    url = resolve_model_url(model_name, ports)
    if url is None:
        raise ModelLoadError(
            f"No reachable Ollama server on {ports} serves {model_name!r}. "
            "Start Ollama (`ollama serve`) and re-run."
        )

    num_gpu = int(config.get("ollama_num_gpu", -1))
    num_ctx = int(config.get("ollama_num_ctx", 8192))
    keep_alive = str(config.get("ollama_keep_alive", "30m"))
    transport = OllamaTransport(
        url,
        model_name,
        num_ctx=num_ctx,
        keep_alive=keep_alive,
        num_gpu=num_gpu,
    )
    options = dict(transport._options())
    options["keep_alive"] = keep_alive

    do_warm = config.get("warm_on_load", True) if warm is None else warm
    warmed = False
    if do_warm:
        say(f"Warming {model_name} into VRAM…")
        warmed = transport.warm()
        if not warmed:
            say(
                f"Warning: warm-up request failed for {model_name!r}; "
                "continuing anyway — the run will surface transport errors."
            )

    size_bytes = record.get("size_bytes")
    budget = vram_budget_bytes(vram_gb if isinstance(vram_gb, (int, float)) else None)
    fits: bool | None = None
    if isinstance(size_bytes, int) and budget is not None:
        fits = size_bytes <= budget

    # One concise human line: model, est. size, VRAM budget, warmed yes/no.
    bits = [f"model: {model_name}"]
    bits.append(
        f"est. {_format_size(size_bytes)}" if isinstance(size_bytes, int)
        else "est. size unknown"
    )
    if isinstance(vram_gb, (int, float)) and vram_gb > 0:
        bits.append(f"VRAM {vram_gb:.1f} GiB (budget {_format_size(budget)})")
        if fits is True:
            bits.append("✓ fits")
        elif fits is False:
            bits.append("! exceeds budget — expect CPU spill, slower")
    bits.append(f"warmed {'yes' if warmed else 'no'}")
    say(" · ".join(bits))

    return LoadedModel(
        name=model_name,
        url=url,
        transport=transport,
        options=options,
        size_bytes=size_bytes if isinstance(size_bytes, int) else None,
        vram_gb=vram_gb if isinstance(vram_gb, (int, float)) else None,
        fits_vram=fits,
        warmed=warmed,
    )


def _format_size(size_bytes: int | None) -> str:
    if not isinstance(size_bytes, int):
        return "unknown size"
    gib = size_bytes / _GIB
    if gib >= 1:
        return f"{gib:.1f} GiB"
    return f"{size_bytes / (1024**2):.0f} MiB"
