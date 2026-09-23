"""OpenAI-compatible adapter: llama-server, vLLM, LM Studio, etc.

Anything serving POST {base}/v1/chat/completions becomes a party member.
"""

from __future__ import annotations

import json
import urllib.request


class OpenAICompatAI:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:8080",
                 api_key: str = "", timeout: int = 120):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages,
                           "stream": False}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions", data=body,
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise IOError(f"chat-completions: bad reply ({str(data)[:200]})")
        if not isinstance(text, str) or not text.strip():
            raise IOError("chat-completions: empty reply")
        return text.strip()
