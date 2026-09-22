"""Tests for harness.gui.specs — stdlib unittest only, no Qt needed."""

import unittest

from harness.gui.specs import (
    analyze_machine,
    describe_spec,
    recommend_models,
    spec_card_html,
)


class AnalyzeMachineTests(unittest.TestCase):
    def test_never_raises_and_has_keys(self):
        spec = analyze_machine()
        for key in ("os", "cpu", "ram_gb", "gpu_name", "vram_gb", "ollama"):
            self.assertIn(key, spec)

    def test_describe_spec_handles_unknowns(self):
        text = describe_spec(
            {"os": "Linux", "cpu": "x", "ram_gb": None,
             "gpu_name": None, "vram_gb": None, "ollama": None}
        )
        self.assertIn("x", text)


class RecommendModelsTests(unittest.TestCase):
    def test_big_vram_tier(self):
        recs = recommend_models({"vram_gb": 24.0})
        self.assertEqual(recs["chat"]["name"], "qwen2.5-coder:32b")
        self.assertEqual(recs["vision"]["name"], "qwen2.5vl:32b")

    def test_mid_vram_tier(self):
        recs = recommend_models({"vram_gb": 12.0})
        self.assertEqual(recs["chat"]["name"], "qwen2.5-coder:14b")
        self.assertEqual(recs["vision"]["name"], "qwen2.5vl:7b")

    def test_8gb_tier(self):
        # The user's RTX 4070 laptop: 8GB VRAM.
        recs = recommend_models({"vram_gb": 8.0})
        self.assertEqual(recs["chat"]["name"], "qwen2.5-coder:7b")
        self.assertEqual(recs["vision"]["name"], "qwen2.5vl:7b")
        self.assertIn("unload", recs["note"])

    def test_cpu_fallback(self):
        recs = recommend_models({"vram_gb": None})
        self.assertEqual(recs["vision"]["name"], "moondream")
        recs = recommend_models({"vram_gb": 0})
        self.assertEqual(recs["chat"]["name"], "qwen2.5-coder:7b")

    def test_never_raises_on_empty_spec(self):
        recs = recommend_models({})
        self.assertIn("chat", recs)
        self.assertIn("vision", recs)


class SpecCardTests(unittest.TestCase):
    def _recs(self):
        return recommend_models({"vram_gb": 8.0})

    def test_missing_models_get_install_links(self):
        html = spec_card_html(
            {"os": "Windows", "cpu": "16 threads", "ram_gb": 31.7,
             "gpu_name": "NVIDIA GeForce RTX 4070 Laptop GPU",
             "vram_gb": 8.0, "ollama": "0.11.4"},
            self._recs(), set(), "qwen2.5-coder:7b", True,
        )
        self.assertIn("RTX 4070", html)
        self.assertIn("ttacode://install-model/qwen2.5vl:7b", html)
        self.assertIn("Best chat engine", html)
        self.assertIn("Best vision engine", html)

    def test_installed_models_show_state(self):
        html = spec_card_html(
            {"os": "Windows", "cpu": "16 threads", "ram_gb": 31.7,
             "gpu_name": "GPU", "vram_gb": 8.0, "ollama": None},
            self._recs(), {"qwen2.5-coder:7b", "qwen2.5vl:7b"},
            "qwen2.5-coder:7b", True,
        )
        self.assertIn("in use now", html)
        self.assertIn("installed", html)
        self.assertNotIn("install-model", html)

    def test_vram_misfit_warns(self):
        html = spec_card_html(
            {"os": "Windows", "cpu": "16 threads", "ram_gb": 31.7,
             "gpu_name": "GPU", "vram_gb": 8.0, "ollama": None},
            self._recs(), {"qwen2.5-coder:32b"},
            "qwen2.5-coder:32b", False,
        )
        self.assertIn("doesn't fit your VRAM", html)


if __name__ == "__main__":
    unittest.main()
