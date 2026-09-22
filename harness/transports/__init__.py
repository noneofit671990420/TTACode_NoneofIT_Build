"""Model transports: how the harness talks to a model server."""

from .ollama import chat, is_reachable

__all__ = ["chat", "is_reachable"]
