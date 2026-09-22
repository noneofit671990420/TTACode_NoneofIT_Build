"""Bridge between the Qt Studio UI and the headless harness.

Stdlib-only, **no Qt imports** — safe to import from ``studio.py`` at
module load time and to bundle into the frozen ``ttacode-studio.exe``.
Never imports ``studio.py`` (no import cycles).

Behavior contract
-----------------
* **Selection** (the Studio model picker) is VRAM-aware:
  :func:`servable_model_choices` lists only models the Ollama transport
  can actually serve (:func:`is_ollama_servable`), with FIT markers
  (``✓`` fits / ``!`` exceeds budget / ``?`` unknown size) against the
  configured ``vram_gb`` budget.
* **Preparation** (at send time) goes through the single harness load
  path (:func:`harness.models.loader.load_model`): resolve, tune
  (``num_gpu`` / ``num_ctx`` / ``keep_alive``), warm into VRAM.
* **The harness never downloads anything.** This replaces
  ``routing.ensure_local_model``'s auto-``ollama pull`` behavior: when
  the requested model is missing or not Ollama-servable,
  :class:`ModelLoadError` propagates and Studio displays its message,
  which already tells the user the exact ``ollama pull`` /
  ``ollama create`` command that fixes it.
"""

from __future__ import annotations

from .models.loader import (
    ModelLoadError,
    detect_vram_gb,
    load_model,
    vram_budget_bytes,
)
from .models.scanner import (
    _capability_score,
    discover_all,
    is_ollama_servable,
    pick_default,
)

__all__ = [
    "ModelLoadError",
    "harness_default_model",
    "prepare_local_model",
    "servable_model_choices",
]

_FIT_ORDER = {"✓": 0, "?": 1, "!": 2}


def _format_size(size_bytes) -> str:
    if not isinstance(size_bytes, int):
        return "unknown size"
    gib = size_bytes / (1024**3)
    if gib >= 1:
        return f"{gib:.1f} GiB"
    return f"{size_bytes / (1024**2):.0f} MiB"


def _effective_vram_gb(config: dict | None) -> float | None:
    """Configured ``vram_gb``, else best-effort ``nvidia-smi`` detection."""
    vram = (config or {}).get("vram_gb")
    if isinstance(vram, (int, float)) and vram > 0:
        return float(vram)
    return detect_vram_gb()


def servable_model_choices(config: dict | None = None) -> list[dict]:
    """Models the Ollama transport can serve, for the Studio picker.

    Each choice is ``{"name", "size_str", "fit"}`` where ``fit`` is
    ``✓`` (fits the 90% VRAM budget), ``!`` (exceeds it — expect CPU
    spill), or ``?`` (size unknown). Sorted: fits first, then the same
    capability ranking :func:`pick_default` uses, then smallest size,
    then name (stable).

    Never raises on missing config keys; ``config=None`` falls back to
    ``nvidia-smi`` detection for the budget.
    """
    budget = vram_budget_bytes(_effective_vram_gb(config))
    choices = []
    for record in discover_all():
        if not is_ollama_servable(record):
            continue
        name = record.get("name", "")
        size = record.get("size_bytes")
        if isinstance(size, int) and budget is not None:
            fit = "✓" if size <= budget else "!"
        else:
            fit = "?"
        choices.append(
            {
                "name": name,
                "size_str": _format_size(size),
                "fit": fit,
                "_score": _capability_score(name),
                "_size_key": size if isinstance(size, int) else 2**63,
            }
        )
    choices.sort(
        key=lambda c: (
            _FIT_ORDER[c["fit"]],
            -c["_score"],
            c["_size_key"],
            c["name"],
        )
    )
    return [
        {"name": c["name"], "size_str": c["size_str"], "fit": c["fit"]}
        for c in choices
    ]


def prepare_local_model(
    name: str | None,
    config: dict | None = None,
    emit=None,
) -> tuple[str, str]:
    """Resolve, tune, and warm ``name``; return ``(url, model_name)``.

    * ``emit`` — optional ``emit(kind, data)`` callable (e.g. the Studio
      event bus); load progress is forwarded as ``('status', message)``.
    * Lets :class:`ModelLoadError` propagate — Studio displays the
      message, which already contains the ``ollama pull`` /
      ``ollama create`` guidance. The harness never downloads.
    """
    def say(message: str) -> None:
        if emit is not None:
            emit("status", message)

    loaded = load_model(name, config, out=say)
    return loaded.url, loaded.name


def harness_default_model(config: dict | None = None) -> str | None:
    """Default model name via the harness heuristic, or ``None``."""
    config = config or {}
    vram = config.get("vram_gb")
    record = pick_default(
        discover_all(),
        vram_gb=vram if isinstance(vram, (int, float)) and vram > 0 else None,
    )
    return record["name"] if record else None
