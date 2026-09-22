"""Thin Ollama transport over plain ``urllib`` (no new dependencies).

Mirrors the style of the original ``app.py::api_chat``: POST JSON to
``/api/chat`` with a bounded context and low temperature. ``stream=True``
yields decoded JSON lines as they arrive.
"""

from __future__ import annotations

import json
import urllib.error
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
    options: dict | None = None,
    keep_alive: str | None = None,
) -> dict | Iterator[dict]:
    """Send a chat request to an Ollama-compatible ``/api/chat`` endpoint.

    Non-streaming returns the decoded response dict. Streaming returns an
    iterator of decoded JSON chunks (each may carry
    ``message.content`` deltas and a final ``done: true`` chunk).

    ``options`` overrides the default Ollama options (``num_ctx``,
    ``temperature``, ``num_gpu`` …); ``keep_alive`` sets the Ollama
    ``keep_alive`` duration (e.g. ``"30m"``) so the model stays warm in
    VRAM between runs — the single biggest latency win on consumer GPUs.
    """
    merged = {"num_ctx": 8192, "temperature": 0.2}
    merged.update(options or {})
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": merged,
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    body = json.dumps(payload).encode()
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


class OllamaTransport:
    """Stateful Ollama chat transport for the agent loop.

    Wraps the module-level :func:`chat` / :func:`is_reachable` with a
    fixed endpoint + model. :meth:`chat` is non-streaming and returns
    ``(content, tool_calls)`` where each tool call is normalized to
    ``{"id": ..., "name": ..., "arguments": dict}`` (Ollama may deliver
    arguments as a JSON string — parsed here, mirroring ``agent_core``).
    """

    def __init__(
        self,
        url: str,
        model: str,
        num_ctx: int = 8192,
        temperature: float = 0.2,
        timeout: float = 600.0,
        keep_alive: str = "30m",
        num_gpu: int = 0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.timeout = timeout
        # keep_alive: how long Ollama keeps the model loaded in VRAM after
        # a request ("30m", "24h", "-1" for forever). Warm models answer
        # in seconds instead of tens of seconds on a 3080/4070.
        self.keep_alive = keep_alive
        # num_gpu: layers offloaded to GPU (0 = Ollama default/auto).
        self.num_gpu = num_gpu

    def is_reachable(self, timeout: float = 3.0) -> bool:
        return is_reachable(self.url, timeout=timeout)

    def warm(self) -> bool:
        """Pre-load the model into VRAM with a minimal request.

        Returns True when the model answered (i.e. it is now warm).
        Used by ``harness models warm`` so the first real run is fast.
        """
        try:
            data = chat(
                self.url,
                self.model,
                [{"role": "user", "content": "ping"}],
                timeout=300.0,
                options=self._options(),
                keep_alive=self.keep_alive,
            )
            return isinstance(data, dict)
        except Exception:
            return False

    def _options(self) -> dict:
        options = {"num_ctx": self.num_ctx, "temperature": self.temperature}
        if self.num_gpu:
            options["num_gpu"] = self.num_gpu
        return options

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> tuple[str, list[dict]]:
        """One chat turn. Returns ``(content, tool_calls)``.

        Raises ``RuntimeError`` / ``OSError`` on transport failure — the
        agent loop converts these into an honest stopped result.
        """
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools or [],
                "stream": False,
                "options": self._options(),
                "keep_alive": self.keep_alive,
            }
        ).encode()
        request = urllib.request.Request(
            self.url + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(1500).decode(errors="replace")
            except Exception:
                detail = ""
            raise RuntimeError(
                f"Ollama {self.url} model={self.model!r}: "
                f"HTTP {exc.code} {exc.reason} {detail}".strip()
            ) from exc
        message = data.get("message", {}) or {}
        content = message.get("content") or ""
        calls: list[dict] = []
        for raw in message.get("tool_calls") or []:
            fn = (raw or {}).get("function") or {}
            arguments = fn.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError:
                    arguments = {"_raw": arguments}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append(
                {
                    "id": raw.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": arguments,
                }
            )
        return content, calls
