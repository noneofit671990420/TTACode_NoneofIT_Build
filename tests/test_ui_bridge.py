"""Tests for harness.ui_bridge — the Qt-free Studio/harness bridge.

No Qt imports anywhere here; the bridge must stay importable without
PySide6 (the frozen console build and these tests prove it).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from harness.ui_bridge import (
    ModelLoadError,
    harness_default_model,
    prepare_local_model,
    servable_model_choices,
)

_GIB = 1024**3

MODELS = [
    {"name": "big-model:20b", "source": "both",
     "size_bytes": int(11.3 * _GIB)},                       # ! exceeds budget
    {"name": "qwen2.5-coder:7b", "source": "both",
     "size_bytes": int(4.4 * _GIB)},                        # ✓ fits
    {"name": "phi4-mini", "source": "live",
     "size_bytes": int(2.5 * _GIB)},                        # ✓ fits
    {"name": "lmstudio-only/Model-GGUF/model-Q4_K_M", "source": "disk",
     "store": "lmstudio", "size_bytes": int(4.7 * _GIB)},    # not servable
    {"name": "unknown-size-live", "source": "live",
     "size_bytes": None},                                   # ? unknown
    {"name": "ollama-disk-model", "source": "disk",
     "store": "ollama", "size_bytes": int(1.9 * _GIB)},      # ✓ fits
]


class ServableChoicesTests(unittest.TestCase):
    def test_filters_fit_markers_and_sort(self):
        with patch("harness.ui_bridge.discover_all", return_value=list(MODELS)):
            choices = servable_model_choices({"vram_gb": 8.0})
        names = [c["name"] for c in choices]
        # LM Studio-only GGUF is not Ollama-servable: excluded.
        self.assertNotIn("lmstudio-only/Model-GGUF/model-Q4_K_M", names)
        # Fits first (capability-ranked: phi4-mini scores highest; note
        # "ollama-disk-model" contains "llama", so it outranks qwen here —
        # the same ranking pick_default uses), then unknown size, then
        # over-budget.
        self.assertEqual(
            names,
            ["phi4-mini", "ollama-disk-model", "qwen2.5-coder:7b",
             "unknown-size-live", "big-model:20b"],
        )
        fits = [c["fit"] for c in choices]
        self.assertEqual(fits, ["✓", "✓", "✓", "?", "!"])
        sizes = [c["size_str"] for c in choices]
        self.assertEqual(
            sizes, ["2.5 GiB", "1.9 GiB", "4.4 GiB", "unknown size", "11.3 GiB"]
        )
        for choice in choices:
            self.assertEqual(set(choice), {"name", "size_str", "fit"})

    def test_lmstudio_only_gives_empty_choices(self):
        lm_only = [m for m in MODELS if m.get("store") == "lmstudio"]
        with patch("harness.ui_bridge.discover_all", return_value=lm_only):
            self.assertEqual(servable_model_choices({"vram_gb": 8.0}), [])

    def test_missing_config_keys_fall_back_to_detection(self):
        with patch("harness.ui_bridge.discover_all", return_value=list(MODELS)), \
             patch("harness.ui_bridge.detect_vram_gb", return_value=None):
            # No vram_gb anywhere and no nvidia-smi: no budget, all "?".
            choices = servable_model_choices({})
        self.assertTrue(choices)
        self.assertTrue(all(c["fit"] == "?" for c in choices))
        # config=None behaves the same.
        with patch("harness.ui_bridge.discover_all", return_value=list(MODELS)), \
             patch("harness.ui_bridge.detect_vram_gb", return_value=None):
            self.assertEqual(
                [c["name"] for c in servable_model_choices()],
                [c["name"] for c in choices],
            )


class PrepareLocalModelTests(unittest.TestCase):
    def test_passes_through_load_model_and_emits_status(self):
        seen = {}

        class FakeLoaded:
            url = "http://127.0.0.1:11434"
            name = "qwen2.5-coder:7b"

        def fake_load(name, config, *, out=None):
            seen["name"] = name
            seen["config"] = config
            out("Warming qwen2.5-coder:7b into VRAM…")
            out("model: qwen2.5-coder:7b · est. 4.4 GiB · warmed yes")
            return FakeLoaded()

        events = []
        with patch("harness.ui_bridge.load_model", fake_load):
            url, model = prepare_local_model(
                "qwen2.5-coder:7b", {"vram_gb": 8.0},
                emit=lambda kind, data: events.append((kind, data)),
            )
        self.assertEqual((url, model),
                         ("http://127.0.0.1:11434", "qwen2.5-coder:7b"))
        self.assertEqual(seen["name"], "qwen2.5-coder:7b")
        self.assertEqual(seen["config"], {"vram_gb": 8.0})
        self.assertEqual(
            events,
            [("status", "Warming qwen2.5-coder:7b into VRAM…"),
             ("status", "model: qwen2.5-coder:7b · est. 4.4 GiB · warmed yes")],
        )

    def test_works_without_emit(self):
        class FakeLoaded:
            url = "http://127.0.0.1:11434"
            name = "qwen2.5-coder:7b"

        with patch("harness.ui_bridge.load_model",
                   lambda name, config, *, out=None: FakeLoaded()):
            self.assertEqual(
                prepare_local_model("qwen2.5-coder:7b", {}),
                ("http://127.0.0.1:11434", "qwen2.5-coder:7b"),
            )

    def test_model_load_error_propagates(self):
        def boom(name, config, *, out=None):
            raise ModelLoadError("Model 'nope' is not on this PC.")

        with patch("harness.ui_bridge.load_model", boom):
            with self.assertRaises(ModelLoadError):
                prepare_local_model("nope", {})


class HarnessDefaultModelTests(unittest.TestCase):
    def test_returns_pick_name(self):
        models = [m for m in MODELS if m["name"] != "lmstudio-only/Model-GGUF/model-Q4_K_M"]
        with patch("harness.ui_bridge.discover_all", return_value=models):
            # 2.5/4.4/1.9 GiB fit the 7.2 GiB budget; the 11.3 GiB model
            # does not. phi4-mini wins on capability score (same ranking
            # pick_default uses).
            self.assertEqual(
                harness_default_model({"vram_gb": 8.0}), "phi4-mini"
            )

    def test_none_when_nothing_servable(self):
        with patch("harness.ui_bridge.discover_all", return_value=[]):
            self.assertIsNone(harness_default_model({"vram_gb": 8.0}))
        lm_only = [m for m in MODELS if m.get("store") == "lmstudio"]
        with patch("harness.ui_bridge.discover_all", return_value=lm_only):
            self.assertIsNone(harness_default_model({}))


if __name__ == "__main__":
    unittest.main()
