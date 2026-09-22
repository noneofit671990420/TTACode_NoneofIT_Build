"""Thin Ollama transport over plain ``urllib`` (no new dependencies).

Mirrors the style of the original ``app.py::api_chat``: POST JSON to
``/api/chat`` with a bounded context and low temperature. ``stream=True``
yields decoded JSON lines as they arrive.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator


def is_reachable(url: str, timeout: float = 3.0) -> bool:
    """True when ``url/api/tags`` answers."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=timeout):
            return True
    except Exception:
        return False


def chat(
    url: str,
    model: str,
    messages: list[dict],
    stream: bool = False,
    timeout: float = 600.0,
) -> dict | Iterator[dict]:
    """Send a chat request to an Ollama-compatible ``/api/chat`` endpoint.

    Non-streaming returns the decoded response dict. Streaming returns an
    iterator of decoded JSON chunks (each may carry
    ``message.content`` deltas and a final ``done: true`` chunk).
    """
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"num_ctx": 8192, "temperature": 0.2},
        }
    ).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    if not stream:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def _iter() -> Iterator[dict]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue

    return _iter()
