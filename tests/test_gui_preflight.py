"""Tests for harness.gui.preflight (Qt-free first-run checks)."""

from __future__ import annotations

import harness.gui.preflight as preflight


def _models():
    return [
        {"name": "qwen2.5-coder:7b", "label": "qwen2.5-coder:7b · 4.7 GB", "fits_vram": True},
        {"name": "moondream:latest", "label": "moondream:latest · 1.7 GB", "fits_vram": True},
    ]


def _recs():
    return {
        "chat": {"name": "qwen2.5-coder:7b", "why": "why-chat"},
        "vision": {"name": "qwen2.5vl:7b", "why": "why-vision"},
    }


def test_pick_startup_model_prefers_recommended_chat():
    assert preflight.pick_startup_model(_models(), _recs()) == "qwen2.5-coder:7b"


def test_pick_startup_model_falls_back_to_vision_then_fits():
    models = [
        {"name": "some-big:70b", "label": "x", "fits_vram": False},
        {"name": "small:3b", "label": "x", "fits_vram": True},
    ]
    # neither recommendation installed -> first that fits VRAM
    assert preflight.pick_startup_model(models, _recs()) == "small:3b"


def test_pick_startup_model_none_when_empty():
    assert preflight.pick_startup_model([], _recs()) is None


def test_collect_preflight_announces_steps_in_order(monkeypatch):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        preflight, "check_ollama", lambda: {"ok": True, "url": "http://x", "installed": True, "version": "v", "hint": ""}
    )
    monkeypatch.setattr(preflight, "list_gui_models", _models)
    monkeypatch.setattr(
        preflight,
        "analyze_machine",
        lambda: {"gpu": "RTX", "vram_gb": 8.0, "ram_gb": 16.0, "cpu": "8 threads", "os": "Windows", "ollama": "v"},
    )
    monkeypatch.setattr(preflight, "recommend_models", lambda spec: _recs())

    result = preflight.collect_preflight(on_step=lambda n, s, d: calls.append((n, s)))

    assert [c[0] for c in calls] == ["ollama", "ollama", "models", "models", "specs", "specs"]
    assert [c[1] for c in calls] == ["running", "ok", "running", "ok", "running", "ok"]
    assert result["startup_model"] == "qwen2.5-coder:7b"
    assert result["ollama"]["ok"] is True
    assert result["spec_line"]
    assert result["error"] == ""


def test_collect_preflight_survives_ollama_down(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "check_ollama",
        lambda: {"ok": False, "url": None, "installed": False, "version": None, "hint": "missing"},
    )
    monkeypatch.setattr(preflight, "analyze_machine", lambda: {"os": "Windows"})
    monkeypatch.setattr(preflight, "recommend_models", lambda spec: _recs())

    result = preflight.collect_preflight()
    assert result["ollama"]["ok"] is False
    assert result["models"] == []
    assert result["startup_model"] is None
    assert "missing" in result["ollama"]["hint"]


def test_collect_preflight_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(preflight, "check_ollama", boom)
    result = preflight.collect_preflight()
    assert result["error"] == "kaboom"


def test_diagnostics_text_includes_key_facts():
    result = {
        "ollama": {"ok": True, "url": "http://127.0.0.1:11434", "version": "0.11.4"},
        "models": [{"name": "qwen2.5-coder:7b", "label": "qwen2.5-coder:7b · 4.7 GB"}],
        "recs": {"chat": {"name": "qwen2.5-coder:7b"}, "vision": {"name": "qwen2.5vl:7b"}},
        "spec_line": "RTX 4070 · 8.0 GB VRAM",
        "error": "",
    }
    text = preflight.diagnostics_text(result, "0.2.4")
    assert "TTACode 0.2.4" in text
    assert "RTX 4070" in text
    assert "http://127.0.0.1:11434" in text
    assert "qwen2.5-coder:7b" in text
    assert "qwen2.5vl:7b" in text
