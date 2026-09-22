"""Discover models already present on the user's PC.

Two discovery channels:

* **Disk scan** (:func:`discover_disk_models`) — reads the standard
  on-disk model stores directly, WITHOUT requiring Ollama (or anything
  else) to be running. Pure filesystem reads, never downloads anything.
* **Live scan** (:func:`discover_live_models`) — queries a running
  Ollama server's ``/api/tags`` endpoint, same approach as the original
  ``routing.py``.

:func:`discover_all` merges both into one list of dicts::

    {"name": "qwen3.5:4b", "source": "disk"|"live"|"both",
     "size_bytes": 3400000000 | None, "path": "..." | None}

Everything here is defensive: missing directories, permission errors and
malformed JSON all yield empty results, never exceptions. A scanner must
never crash first-run setup on a machine it has never seen before.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

# File extensions that commonly hold model weights in LM-Studio-style stores.
_GGUF_EXTENSIONS = {".gguf", ".bin", ".safetensors", ".pt", ".onnx"}


def _home() -> Path:
    """User home, honoring USERPROFILE on Windows like the rest of the repo."""
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    return Path(os.path.expanduser("~"))


def _ollama_store() -> Path:
    """"Standard Ollama model store for this platform."""
    return _home() / ".ollama" / "models"


def _lmstudio_store() -> Path:
    """Standard LM Studio model store (same layout on all platforms)."""
    return _home() / ".lmstudio" / "models"


def _safe_stat_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _scan_ollama_store(store: Path) -> list[dict]:
    """Parse an Ollama ``models/`` directory into model records.

    Ollama layout::

        models/
            manifests/registry.ollama.ai/library/<name>/<tag>   (JSON)
            blobs/sha256:<digest>                               (weight blobs)

    The manifest JSON references blobs by digest; we sum the blob sizes to
    get an approximate on-disk size for the model.
    """
    results: list[dict] = []
    manifests = store / "manifests" / "registry.ollama.ai" / "library"
    blobs = store / "blobs"
    try:
        model_dirs = [d for d in manifests.iterdir() if d.is_dir()]
    except OSError:
        return []
    for model_dir in sorted(model_dirs):
        try:
            tag_files = [t for t in model_dir.iterdir() if t.is_file()]
        except OSError:
            continue
        for tag_file in sorted(tag_files):
            tag = tag_file.name
            name = f"{model_dir.name}:{tag}" if tag != "latest" else model_dir.name
            try:
                manifest = json.loads(tag_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                continue
            size = 0
            seen = False
            layers = manifest.get("layers", [])
            if not isinstance(layers, list):
                layers = []
            config = manifest.get("config")
            candidates = list(layers)
            if isinstance(config, dict):
                candidates.append(config)
            for layer in candidates:
                if not isinstance(layer, dict):
                    continue
                digest = layer.get("digest", "")
                if not isinstance(digest, str) or not digest.startswith("sha256:"):
                    continue
                blob_size = _safe_stat_size(blobs / digest.replace(":", "-"))
                if blob_size is None:
                    # Some Ollama versions keep the raw digest as filename.
                    blob_size = _safe_stat_size(blobs / digest)
                if blob_size is not None:
                    size += blob_size
                    seen = True
            results.append(
                {
                    "name": name,
                    "source": "disk",
                    "store": "ollama",
                    "size_bytes": size if seen else None,
                    "path": str(store),
                }
            )
    return results


# Filename fragments that are *not* loadable language models, even though
# they live in model stores (e.g. vision projector weights shipped next
# to the LLM). Matching is case-insensitive and substring-based.
_NON_MODEL_FRAGMENTS = ("mmproj",)


def _scan_lmstudio_store(store: Path) -> list[dict]:
    """Recursively find model weight files in an LM Studio-style store.

    Skips non-model artifacts such as ``mmproj`` vision projectors —
    they are not loadable LLMs and must never become default picks.
    """
    results: list[dict] = []
    try:
        candidates = list(store.rglob("*"))
    except OSError:
        return []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _GGUF_EXTENSIONS:
            continue
        lowered = path.name.lower()
        if any(frag in lowered for frag in _NON_MODEL_FRAGMENTS):
            continue
        try:
            rel = path.relative_to(store)
        except ValueError:
            continue
        name = rel.with_suffix("").as_posix()  # e.g. "author/model-q4_k_m"
        results.append(
            {
                "name": name,
                "source": "disk",
                "store": "lmstudio",
                "size_bytes": _safe_stat_size(path),
                "path": str(path),
            }
        )
    return results


def discover_disk_models(
    ollama_store: Path | None = None,
    lmstudio_store: Path | None = None,
) -> list[dict]:
    """Scan on-disk model stores. Never raises on missing/unreadable dirs."""
    stores = [
        (ollama_store if ollama_store is not None else _ollama_store(), _scan_ollama_store),
        (lmstudio_store if lmstudio_store is not None else _lmstudio_store(), _scan_lmstudio_store),
    ]
    results: list[dict] = []
    for store, scanner in stores:
        try:
            results.extend(scanner(store))
        except Exception:
            # Belt and braces: a scanner must never take down the CLI.
            continue
    return results


def _live_tags(port: int, timeout: float = 3.0) -> list[str]:
    """Model names from a running Ollama ``/api/tags`` endpoint."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/tags", timeout=timeout
        ) as response:
            data = json.load(response)
    except Exception:
        return []
    models = data.get("models", []) if isinstance(data, dict) else []
    names = []
    for entry in models:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return names


def discover_live_models(ports: tuple[int, ...] = (11434, 11435)) -> dict[int, list[str]]:
    """Query live Ollama servers. Unreachable ports yield empty lists."""
    return {port: _live_tags(port) for port in ports}


def discover_all(
    ports: tuple[int, ...] = (11434, 11435),
    ollama_store: Path | None = None,
    lmstudio_store: Path | None = None,
) -> list[dict]:
    """Merge disk and live discovery into one de-duplicated list.

    A model present in both channels gets ``source="both"``. Disk size
    wins when both are known (live ``/api/tags`` also reports sizes, but
    we keep this merge simple and honest about where data came from).
    """
    disk = discover_disk_models(ollama_store, lmstudio_store)
    live = discover_live_models(ports)

    merged: dict[str, dict] = {}
    for record in disk:
        merged[record["name"]] = dict(record)

    for _port, names in live.items():
        for name in names:
            base = name.split(":")[0]
            # Match "name" against "name:tag" / "name:latest" disk entries.
            key = name if name in merged else (base if base in merged else name)
            if key in merged:
                merged[key]["source"] = "both"
            else:
                merged[name] = {
                    "name": name,
                    "source": "live",
                    "size_bytes": None,
                    "path": None,
                }
    return [merged[key] for key in sorted(merged)]


def _capability_score(name: str) -> int:
    """Heuristic score for "likely a good default local agent model".

    Rewards (higher is better):
    * instruct / chat-tuned variants — follow tool instructions better
    * known tool-capable families (qwen, phi, granite) seen in the wild
    * compact "mini"/small sizes — actually runnable on a normal PC

    This is a heuristic, not a benchmark. It only orders candidates that
    are already downloaded; it never triggers a download.
    """
    lowered = name.lower()
    score = 0
    if "instruct" in lowered:
        score += 3
    if "tool" in lowered:
        score += 2
    for family in ("qwen", "phi", "granite", "mistral", "llama"):
        if family in lowered:
            score += 2
            break
    if "mini" in lowered or "small" in lowered:
        score += 1
    if "27b" in lowered or "30b" in lowered or "70b" in lowered:
        score -= 2  # probably too heavy to be a *default* on unknown hardware
    if "embed" in lowered:
        score -= 10  # embedding models cannot chat
    if "mmproj" in lowered:
        score -= 10  # vision projector, not a loadable LLM (belt & braces;
        # the LM Studio scanner already filters these out)
    return score


def is_ollama_servable(record: dict) -> bool:
    """Can the Ollama transport serve this discovery record?

    The harness only speaks the Ollama API, so a default pick must be a
    model an Ollama server can actually load: one it already serves
    (``source`` ``"both"``/``"live"``), or one sitting in Ollama's own
    blob store (``source == "disk"`` with ``store == "ollama"`` — it
    becomes servable as soon as ``ollama serve`` runs). A bare GGUF in
    the LM Studio folder is *not* servable until imported with
    ``ollama create``.
    """
    source = record.get("source")
    if source in ("both", "live"):
        return True
    # Records predating the "store" key came from the Ollama scanner.
    return source == "disk" and record.get("store", "ollama") == "ollama"


# Default vision model offered by the GUI's one-click installer.
# A config ``vision_model`` value overrides this for power users.
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"

_VISION_MARKERS = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "cogvlm",
    "vision",
    "-vl",
    "vl:",
    "_vl",
    "qwen2-vl",
    "qwen2.5vl",
    "qwen-vl",
)


def is_vision_model(name: str) -> bool:
    """Heuristic: does this model name look vision-capable?

    Ollama exposes no capability flags, so we match known vision
    families/markers in the name. Conservative on purpose — a miss just
    means the GUI tries the request and reports Ollama's honest error.
    """
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _VISION_MARKERS)


def pick_default(
    models: list[dict],
    vram_gb: float | None = None,
    transport: str = "ollama",
) -> dict | None:
    """Pick a sane default model from discovered models.

    Heuristic (documented, deterministic):

    1. Consider only models the active transport can serve
       (:func:`is_ollama_servable` for ``transport="ollama"``) — a
       default must actually load, not just exist on disk. A bare GGUF
       in the LM Studio folder is skipped here; import it with
       ``ollama create`` to make it eligible.
    2. When ``vram_gb`` is given, prefer servable models whose known
       size fits in 90% of VRAM (headroom for context/KV cache plus OS
       overhead). A model that fits fully in VRAM is dramatically faster
       than one spilling to system RAM, so fit wins over raw capability.
       Models with unknown size are never excluded — they simply can't
       win the fit check and stay eligible as fallbacks.
    3. Rank by capability score (:func:`_capability_score`).
    4. Break ties by smallest known size (fits more machines).
    5. Final tie-break: alphabetical name, so the choice is stable.

    If nothing fits the VRAM budget, the heuristic falls back to the
    plain ranking over all servable models (smallest capable first) —
    callers should warn that CPU spill is expected (see
    ``harness.models.loader``).

    Returns the winning record, or ``None`` when nothing servable is
    downloaded.
    """
    if transport == "ollama":
        candidates = [m for m in models if is_ollama_servable(m)]
    else:
        candidates = [m for m in models if m.get("source") in ("disk", "both")]
    if not candidates:
        return None

    def rank(record: dict) -> tuple:
        size = record.get("size_bytes")
        # Unknown size sorts after known sizes of equal score.
        size_key = size if isinstance(size, int) else 2**63
        return (-_capability_score(record.get("name", "")), size_key, record.get("name", ""))

    if vram_gb is not None and vram_gb > 0:
        budget_bytes = int(vram_gb * 0.9 * (1024**3))
        fits = [
            m for m in candidates
            if isinstance(m.get("size_bytes"), int)
            and m["size_bytes"] <= budget_bytes
        ]
        if fits:
            return sorted(fits, key=rank)[0]
        # Nothing fits: fall through to the plain ranking; the caller
        # warns about expected CPU spill.

    return sorted(candidates, key=rank)[0]
