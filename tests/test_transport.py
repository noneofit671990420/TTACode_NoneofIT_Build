"""Unit tests for harness.transports.ollama — stdlib unittest only."""

import io
import unittest
import urllib.error
from unittest import mock

from harness.transports.ollama import OllamaTransport, unload_model


def _http_error(url, code=404):
    return urllib.error.HTTPError(
        url, code, "Not Found", {}, io.BytesIO(b'{"error":"model not found"}')
    )


class TestOllamaTransportErrors(unittest.TestCase):
    def test_http_error_carries_url_and_model(self):
        t = OllamaTransport("http://127.0.0.1:11434", "ghost:99b")
        with mock.patch(
            "harness.transports.ollama.urllib.request.urlopen",
            side_effect=_http_error("http://127.0.0.1:11434/api/chat"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                t.chat([{"role": "user", "content": "hi"}])
        msg = str(ctx.exception)
        self.assertIn("http://127.0.0.1:11434", msg)
        self.assertIn("ghost:99b", msg)
        self.assertIn("404", msg)


class TestUnloadModel(unittest.TestCase):
    def test_unload_posts_generate_with_keep_alive_zero(self):
        seen = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["body"] = request.data.decode()
            return FakeResponse()

        with mock.patch(
            "harness.transports.ollama.urllib.request.urlopen", fake_urlopen
        ):
            self.assertTrue(unload_model("http://127.0.0.1:11434", "old:7b"))
        self.assertTrue(seen["url"].endswith("/api/generate"))
        import json

        body = json.loads(seen["body"])
        self.assertEqual(body["model"], "old:7b")
        self.assertEqual(body["keep_alive"], 0)

    def test_unload_never_raises(self):
        with mock.patch(
            "harness.transports.ollama.urllib.request.urlopen",
            side_effect=OSError("down"),
        ):
            self.assertFalse(unload_model("http://127.0.0.1:11434", "old:7b"))


if __name__ == "__main__":
    unittest.main()
