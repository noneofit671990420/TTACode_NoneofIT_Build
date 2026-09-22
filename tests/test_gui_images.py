"""Tests for harness.gui.images — Qt is optional (fallback path tested)."""

import base64
import tempfile
import unittest
from pathlib import Path

from harness.gui.images import encode_image_file, is_image_file


class ImageHelperTests(unittest.TestCase):
    def test_is_image_file(self):
        self.assertTrue(is_image_file("photo.PNG"))
        self.assertTrue(is_image_file(Path("a/b.webp")))
        self.assertFalse(is_image_file("notes.txt"))
        self.assertFalse(is_image_file("archive.zip"))

    def test_encode_missing_file_raises(self):
        with self.assertRaises(ValueError):
            encode_image_file("/nonexistent/dir/x.png")

    def test_encode_rejects_non_image_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            fh.write(b"hello")
            name = fh.name
        try:
            with self.assertRaises(ValueError):
                encode_image_file(name)
        finally:
            Path(name).unlink()

    def test_encode_returns_base64(self):
        # Minimal valid PNG (1x1). Without Qt this exercises the raw-bytes
        # fallback; with Qt it may re-encode as JPEG — either way the
        # contract is "non-empty base64 string".
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(png)
            name = fh.name
        try:
            payload = encode_image_file(name)
            self.assertTrue(payload)
            base64.b64decode(payload)  # valid base64
        finally:
            Path(name).unlink()


if __name__ == "__main__":
    unittest.main()
