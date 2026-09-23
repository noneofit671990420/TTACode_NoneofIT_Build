"""Mus.eng — easy packages so any local AI can join the party.

A safe, tiny protocol for local-AI-to-local-AI (and Muse-to-box)
communication. Stdlib only.

Security contract (same as the Bonsai-Geronimo-Branc wire it grew from):
  * UTF-8 JSON, one \\n-terminated object per statement ("ASCII ENTER"
    framing — a stream resynchronizes after garbage).
  * Every envelope carries hmac = HMAC-SHA256(PSK, canonical JSON of all
    other fields). One flipped bit anywhere -> frame dropped.
  * Per-sender boot id + monotonic seq + message-id dedup -> no replays.
  * Transports are untrusted: the HMAC is the auth, not the pipe.

What Mus.eng adds on top:
  * Addressing: every envelope has "to" — a peer name or "*" (the party).
  * Task frames: task-propose / task-decision / task-result. A remote peer
    (e.g. Muse) can propose work; NOTHING executes without the local
    gatekeeper's approval. The local AI — or the human — is the gatekeeper.
  * announce: peers introducing themselves to the party.

Text pipe only, by default. Inbound text is never executed. Task execution
happens only after gatekeeper approval, jailed to a work root, with a
command allowlist. See gatekeeper.py.
"""

from museng import wire, gatekeeper, party  # noqa: F401

__version__ = "0.1.0"
__all__ = ["wire", "gatekeeper", "party"]
