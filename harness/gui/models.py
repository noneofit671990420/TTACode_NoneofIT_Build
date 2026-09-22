"""Model-list helpers for the GUI dropdown. Stdlib + harness only."""

from __future__ import annotations

from harness.models import (
    detect_vram_gb,
    discover_all,
    is_ollama_servable,
    is_vision_model,
)


def format_size(size_bytes: int | None) -> str:
    if not isinstance(size_bytes, int):
        return "size unknown"
    gib = size_bytes / (1024**3)
    if gib >= 1:
        return f"{gib:.1f} GiB"
    return f"{size_bytes / (1024**2):.0f} MiB"


def list_gui_models() -> list[dict]:
    """Ollama-servable installed models, best default first.

    Each entry: ``{"name", "label", "vision", "fits_vram"}``. ``label``
    is the human-readable dropdown text.
    """
    try:
        records = discover_all()
    except Exception:
        return []
    vram_gb = None
    try:
        vram_gb = detect_vram_gb()
    except Exception:
        pass
    budget = int(vram_gb * 0.9 * (1024**3)) if vram_gb else None

    entries = []
    for record in records:
        if not is_ollama_servable(record):
            continue
        name = record.get("name") or ""
        if not name:
            continue
        size = record.get("size_bytes")
        fits = budget is not None and isinstance(size, int) and size <= budget
        vision = is_vision_model(name)
        bits = [format_size(size)]
        bits.append("fits VRAM" if fits else "may not fit VRAM" if budget else "")
        if vision:
            bits.append("vision")
        label = f"{name} ({', '.join(b for b in bits if b)})"
        entries.append(
            {
                "name": name,
                "label": label,
                "vision": vision,
                "fits_vram": fits,
                "size_bytes": size if isinstance(size, int) else 0,
            }
        )
    # Best default first: fits VRAM, then smaller, then name.
    entries.sort(key=lambda e: (not e["fits_vram"], e["size_bytes"], e["name"]))
    seen: set[str] = set()
    unique = []
    for entry in entries:
        if entry["name"] not in seen:
            seen.add(entry["name"])
            unique.append(entry)
    return unique


def find_vision_model(models: list[dict] | None = None) -> dict | None:
    """First servable vision-capable model, or None."""
    for entry in models if models is not None else list_gui_models():
        if entry["vision"]:
            return entry
    return None
