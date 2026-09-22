"""Qt-free first-run checks for the precheck dialog.

``collect_preflight()`` runs Ollama → models → machine-spec checks in order
and returns one plain dict the dialog displays. Everything here is stdlib +
harness only so it can be unit tested without Qt; the dialog itself lives in
``harness/gui/precheck.py`` and runs this in a worker thread.

The rule: the app must never strand the user in an empty window. Every
failure here maps to a guided fix (install Ollama, one-click model install).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Callable

from harness.gui.models import list_gui_models
from harness.gui.specs import analyze_machine, describe_spec, recommend_models
from harness.transports.ollama import is_reachable

OLLAMA_PORTS: tuple[int, ...] = (11434, 11435)
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"


def _ollama_version() -> str | None:
    exe = shutil.which("ollama")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        return out.splitlines()[0] if out else None
    except Exception:
        return None


def check_ollama() -> dict:
    """Is an Ollama server answering? Installed at all?

    Returns ``{"ok", "url", "installed", "version", "hint"}``. Never raises.
    """
    try:
        installed = shutil.which("ollama") is not None
        version = _ollama_version()
        for port in OLLAMA_PORTS:
            url = f"http://127.0.0.1:{port}"
            try:
                if is_reachable(url):
                    return {
                        "ok": True,
                        "url": url,
                        "installed": True,
                        "version": version,
                        "hint": "",
                    }
            except Exception:
                continue
        if installed:
            hint = (
                "Ollama is installed but isn't answering. "
                "Start it (the Ollama app, or run `ollama serve`), then Retry."
            )
        else:
            hint = (
                "Ollama isn't installed. TTACode needs Ollama — the free "
                "app that runs the models on your PC."
            )
        return {
            "ok": False,
            "url": None,
            "installed": installed,
            "version": version,
            "hint": hint,
        }
    except Exception as exc:  # never break the precheck itself
        return {
            "ok": False,
            "url": None,
            "installed": False,
            "version": None,
            "hint": f"Could not check Ollama: {exc}",
        }


def start_ollama_server() -> bool:
    """Best-effort ``ollama serve`` in the background. Returns True if launched."""
    exe = shutil.which("ollama")
    if not exe:
        return False
    try:
        kwargs: dict = {}
        try:
            # Windows: don't pop a console window, detach from the GUI.
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        except AttributeError:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        return True
    except Exception:
        return False


def pick_startup_model(models: list[dict], recs: dict) -> str | None:
    """Which installed model should the session warm up?

    Prefers the recommended chat engine, then the recommended vision
    engine (it chats too), then anything that fits VRAM, then anything.
    """
    names = [m["name"] for m in models]
    if not names:
        return None
    for key in ("chat", "vision"):
        want = (recs.get(key) or {}).get("name")
        if want and want in names:
            return want
    for m in models:
        if m.get("fits_vram"):
            return m["name"]
    return names[0]


def collect_preflight(
    on_step: Callable[[str, str, str], None] | None = None,
) -> dict:
    """Run every first-run check in order; announce each step.

    ``on_step(name, status, detail)`` — status is ``running``/``ok``/``fail``.
    Never raises; a failed check just records its guided hint.
    """
    result: dict = {
        "ollama": {},
        "models": [],
        "spec": {},
        "recs": {},
        "startup_model": None,
        "error": "",
    }

    def say(name: str, status: str, detail: str = "") -> None:
        if on_step is not None:
            try:
                on_step(name, status, detail)
            except Exception:
                pass

    try:
        say("ollama", "running", "Checking Ollama…")
        ollama = check_ollama()
        result["ollama"] = ollama
        say(
            "ollama",
            "ok" if ollama["ok"] else "fail",
            f"Ollama {ollama['version'] or ''} — running".strip()
            if ollama["ok"]
            else ollama["hint"],
        )

        say("models", "running", "Scanning installed models…")
        models = list_gui_models() if ollama["ok"] else []
        result["models"] = models
        say(
            "models",
            "ok" if models else "fail",
            f"{len(models)} model(s) installed"
            if models
            else "No models installed yet",
        )

        say("specs", "running", "Analyzing your machine…")
        spec = analyze_machine()
        recs = recommend_models(spec)
        result["spec"] = spec
        result["recs"] = recs
        result["spec_line"] = describe_spec(spec)
        say("specs", "ok", describe_spec(spec))

        result["startup_model"] = pick_startup_model(models, recs)
    except Exception as exc:  # the precheck itself must never crash
        result["error"] = str(exc)
        say("specs", "fail", str(exc))
    return result


def diagnostics_text(result: dict, app_version: str) -> str:
    """Plain-text diagnostics the user can paste into a bug report."""
    ollama = result.get("ollama", {})
    lines = [
        f"TTACode {app_version}",
        f"Machine: {result.get('spec_line', 'unknown')}",
        f"Ollama: {'ok @ ' + str(ollama.get('url')) if ollama.get('ok') else 'NOT RUNNING'}"
        + (f" ({ollama.get('version')})" if ollama.get("version") else ""),
        f"Models: {len(result.get('models', []))} installed",
    ]
    for m in result.get("models", []):
        lines.append(f"  - {m.get('name')} ({m.get('label', '')})")
    recs = result.get("recs", {})
    if recs:
        lines.append(
            f"Recommended: chat={recs.get('chat', {}).get('name')} "
            f"vision={recs.get('vision', {}).get('name')}"
        )
    if result.get("error"):
        lines.append(f"Precheck error: {result['error']}")
    return "\n".join(lines)
