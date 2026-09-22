"""Model discovery + loading subpackage.

:mod:`scanner` finds models already on the user's PC (disk + live).
:mod:`loader` is the single load path: resolve → tune → warm → run.
"""

from .loader import (
    LoadedModel,
    ModelLoadError,
    detect_vram_gb,
    load_model,
    resolve_model_url,
    vram_budget_bytes,
)
from .scanner import (
    DEFAULT_VISION_MODEL,
    discover_all,
    discover_disk_models,
    discover_live_models,
    is_ollama_servable,
    is_vision_model,
    pick_default,
)

__all__ = [
    "DEFAULT_VISION_MODEL",
    "LoadedModel",
    "ModelLoadError",
    "detect_vram_gb",
    "discover_all",
    "discover_disk_models",
    "discover_live_models",
    "is_ollama_servable",
    "is_vision_model",
    "load_model",
    "pick_default",
    "resolve_model_url",
    "vram_budget_bytes",
]
