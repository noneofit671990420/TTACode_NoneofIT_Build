"""Machine spec analysis + model recommendations for the GUI.

Stdlib only. Runs once at launch (fast, best-effort, never raises) and
answers: what is this machine, and which Ollama models are the best
fit for agentic chat work and for vision on it?

Model sizes below assume Q4_K_M-ish quantization plus context headroom.
They are recommendations, not guarantees — Ollama reports the honest
error if a pick doesn't fit.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

# (chat_model, vision_model, min_vram_gb, blurb)
_TIERS = (
    (
        "qwen2.5-coder:32b",
        "qwen2.5vl:32b",
        24,
        "Big VRAM — large models fit with context headroom.",
    ),
    (
        "qwen2.5-coder:14b",
        "qwen2.5vl:7b",
        12,
        "Mid-range VRAM — strong coding model, nimble vision model.",
    ),
    (
        "qwen2.5-coder:7b",
        "qwen2.5vl:7b",
        7,
        "8GB-class VRAM — keep one model warm at a time. "
        "qwen2.5vl:7b alone covers chat, vision, and tools.",
    ),
)

_CPU_CHAT_MODEL = "qwen2.5-coder:7b"
_CPU_VISION_MODEL = "moondream"
_CPU_BLURB = (
    "No usable GPU detected — these run on CPU (slower, but they run). "
    "moondream answers about images but can't use tools."
)


def _ram_gb() -> float | None:
    """Best-effort total system RAM in GiB. Never raises."""
    try:
        system = platform.system()
        if system == "Windows":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemStatus()
            status.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / (1024**3), 1)
            return None
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        kb = float(line.split()[1])
                        return round(kb / (1024**2), 1)
            return None
        if system == "Darwin":
            proc = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                return round(float(proc.stdout.strip()) / (1024**3), 1)
            return None
    except Exception:
        return None
    return None


def _gpu() -> tuple[str | None, float | None]:
    """Best-effort (gpu_name, vram_gb) via nvidia-smi. Never raises."""
    try:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None, None
        proc = subprocess.run(
            [exe, "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None, None
        line = proc.stdout.strip().splitlines()
        if not line:
            return None, None
        parts = [p.strip() for p in line[0].split(",")]
        name = parts[0] if parts and parts[0] else None
        vram = None
        if len(parts) > 1:
            try:
                vram = round(float(parts[1].split()[0]) / 1024, 1)
            except (ValueError, IndexError):
                vram = None
        return name, vram
    except Exception:
        return None, None


def _ollama_version() -> str | None:
    try:
        exe = shutil.which("ollama")
        if not exe:
            return None
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            # "ollama version is 0.11.4" -> "0.11.4"
            text = proc.stdout.strip()
            return text.rsplit(" ", 1)[-1] or None
    except Exception:
        return None
    return None


def analyze_machine() -> dict:
    """Snapshot of this machine's specs. Never raises."""
    gpu_name, vram_gb = _gpu()
    cpu_count = os.cpu_count() or 0
    return {
        "os": platform.system(),
        "cpu": f"{cpu_count} threads" if cpu_count else "unknown CPU",
        "ram_gb": _ram_gb(),
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
        "ollama": _ollama_version(),
    }


def recommend_models(spec: dict) -> dict:
    """Best chat + vision picks for this machine.

    Returns ``{"chat": {"name", "reason"}, "vision": {...},
    "note": str}``. Never raises.
    """
    vram = spec.get("vram_gb")
    if isinstance(vram, (int, float)) and vram > 0:
        for chat, vision, minimum, blurb in _TIERS:
            if vram >= minimum:
                note = blurb
                if vram < 12:
                    note += (
                        " Tip: switching models keeps the old one warm for "
                        "30 minutes — on 8GB cards that can thrash, so TTACode "
                        "unloads the previous model on switch."
                    )
                return {
                    "chat": {"name": chat, "reason": blurb},
                    "vision": {"name": vision, "reason": blurb},
                    "note": note,
                }
    return {
        "chat": {"name": _CPU_CHAT_MODEL, "reason": _CPU_BLURB},
        "vision": {"name": _CPU_VISION_MODEL, "reason": _CPU_BLURB},
        "note": _CPU_BLURB,
    }


def describe_spec(spec: dict) -> str:
    """One-line human summary, e.g. 'RTX 4070 Laptop · 8.0 GB VRAM · 31.7 GB RAM'."""
    bits = []
    if spec.get("gpu_name"):
        bits.append(spec["gpu_name"])
    vram = spec.get("vram_gb")
    if isinstance(vram, (int, float)):
        bits.append(f"{vram:.1f} GB VRAM")
    ram = spec.get("ram_gb")
    if isinstance(ram, (int, float)):
        bits.append(f"{ram:.1f} GB RAM")
    bits.append(spec.get("cpu") or "unknown CPU")
    if spec.get("ollama"):
        bits.append(f"Ollama {spec['ollama']}")
    return " · ".join(bits) if bits else "unknown machine"


def spec_card_html(
    spec: dict,
    recs: dict,
    installed: set[str],
    current: str,
    current_fits: bool | None,
) -> str:
    """HTML card: machine summary + best-model recommendations.

    Missing recommended models get a one-click
    ``ttacode://install-model/<name>`` install link.
    """
    rows = []
    for role in ("chat", "vision"):
        rec = recs[role]
        name = rec["name"]
        label = "Best chat engine" if role == "chat" else "Best vision engine"
        if name in installed:
            state = "in use now ✓" if name == current else "installed ✓ — pick it from the model menu"
        else:
            state = (
                f'<a href="ttacode://install-model/{name}">'
                f"⬇ Install {name}</a> (one click)"
            )
        rows.append(
            f"<p><b>{label}:</b> <code>{name}</code><br>"
            f"{rec['reason']}<br>{state}</p>"
        )
    warn = ""
    if current_fits is False:
        warn = (
            f"<p>⚠ <code>{current}</code> probably doesn't fit your VRAM — "
            "Ollama will spill to CPU and everything crawls. "
            "The picks above are sized for this machine.</p>"
        )
    return (
        '<div class="msg notice"><div class="who">TTACODE</div>'
        "<p><b>Your machine:</b> " + describe_spec(spec) + "</p>"
        + "".join(rows)
        + warn
        + (f"<p><i>{recs['note']}</i></p>" if recs.get("note") else "")
        + "</div>"
    )
