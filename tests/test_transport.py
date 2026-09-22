"""Unit tests for harness.transports.ollama — stdlib unittest only."""

import io
import unittest
import urllib.error
from unittest import mock

from harness.transports.ollama import OllamaTransport


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


if __name__ == "__main__":
    unittest.main()
