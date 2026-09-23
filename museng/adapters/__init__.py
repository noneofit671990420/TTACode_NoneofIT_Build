"""Mus.eng adapters: wrappers that let any local AI speak at the party.

Each AI adapter exposes one method:

    complete(messages) -> str     # messages: [{"role","content"}...]

The party host feeds inbound party chat to the model and broadcasts
whatever it says back. The model never sees the wire — the adapter
is its invitation.
"""

from .ollama import OllamaAI
from .openai_compat import OpenAICompatAI

__all__ = ["OllamaAI", "OpenAICompatAI"]
