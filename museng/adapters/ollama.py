"""Ollama adapter: any `ollama run <model>` becomes a party member."""

from __future__ import annotations

import json
import urllib.request


class OllamaAI:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434",
                 timeout: int = 120):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages,
                           "stream": False}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        msg = data.get("message", {})
        text = msg.get("content", "")
        if not isinstance(text, str) or not text.strip():
            raise IOError(f"ollama: empty reply ({str(data)[:200]})")
        return text.strip()
