"""Tests for harness.gui.models vision preference — no Qt needed."""

import unittest

from harness.gui.models import find_vision_model


def _entry(name, vision=True):
    return {
        "name": name,
        "label": name,
        "vision": vision,
        "fits_vram": True,
        "size_bytes": 1,
    }


class FindVisionModelTests(unittest.TestCase):
    def test_prefers_tool_capable_vision_model(self):
        models = [_entry("moondream:latest"), _entry("qwen2.5vl:7b")]
        found = find_vision_model(models)
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "qwen2.5vl:7b")

    def test_falls_back_to_chat_only_vision_model(self):
        models = [_entry("llava:7b")]
        found = find_vision_model(models)
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "llava:7b")

    def test_none_when_no_vision_models(self):
        models = [_entry("qwen2.5-coder:7b", vision=False)]
        self.assertIsNone(find_vision_model(models))


if __name__ == "__main__":
    unittest.main()
