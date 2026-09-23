"""Mus.eng wire protocol: envelopes, addressing, task frames.

Self-contained (no dependency on skills/desk-link): this is the
generalized v2 of that wire. v=0 envelopes stay cross-compatible —
a v1 desk-link peer will verify and simply ignore frames whose type
it doesn't know.

Envelope fields (all HMAC'd):
  v, type, from, to ("*" = the whole party), boot, id, seq, ts, hmac
  + type-specific payload (text, task_*, ...).

Frame types:
  chat            {text}
  ping / pong     {ping_id}
  hello/welcome/ready   (direct-transport PSK handshake, unchanged)
  announce        {peer, capabilities:[...]}  -- "hi party, I'm here"
  task-propose    {task_id, kind, spec}        -- "please do this"
  task-decision   {task_id, approved, reason} -- gatekeeper's verdict
  task-result     {task_id, ok, output, reason?}

Task kinds (see gatekeeper.py for what may execute):
  write-file {path, content} | read-file {path} | list-dir {path}
  | run {cmd:[...], cwd?}
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import threading
import time
import uuid

VERSION = 0
PARTY = "*"

TASK_TYPES = {"task-propose", "task-decision", "task-result"}
TASK_KINDS = {"write-file", "read-file", "list-dir", "run"}


# --------------------------------------------------------------------------
# Envelopes
# --------------------------------------------------------------------------

def _canon(fields: dict) -> bytes:
    return json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_hmac(psk_hex: str, fields: dict) -> str:
    return _hmac.new(
        bytes.fromhex(psk_hex), _canon(fields), hashlib.sha256
    ).hexdigest()


def make_boot_id() -> str:
    return uuid.uuid4().hex[:16]


def build_envelope(
    psk_hex: str,
    *,
    sender: str,
    boot: str,
    seq: int,
    to: str = PARTY,
    type_: str = "chat",
    text: str | None = None,
    extra: dict | None = None,
) -> dict:
    fields: dict = {
        "v": VERSION,
        "type": type_,
        "from": sender,
        "to": to,
        "boot": boot,
        "id": uuid.uuid4().hex,
        "seq": int(seq),
        "ts": int(time.time()),
    }
    if extra:
        fields.update(extra)
    if text is not None:
        fields["text"] = text
    fields["hmac"] = compute_hmac(psk_hex, fields)
    return fields


def verify_envelope(psk_hex: str, obj) -> dict | None:
    """Return the fields (minus hmac) when valid, else None."""
    if not isinstance(obj, dict):
        return None
    got = obj.get("hmac")
    if not isinstance(got, str):
        return None
    fields = {k: v for k, v in obj.items() if k != "hmac"}
    if fields.get("v") != VERSION:
        return None
    expected = compute_hmac(psk_hex, fields)
    if not _hmac.compare_digest(got, expected):
        return None
    return fields


def encode_line(fields: dict) -> bytes:
    return (
        json.dumps(fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        + b"\n"
    )


def proof(psk_hex: str, nonce: str) -> str:
    """Handshake proof: proves knowledge of the PSK for a fresh nonce."""
    return compute_hmac(psk_hex, {"proof": nonce})


# --------------------------------------------------------------------------
# Framing / dedup
# --------------------------------------------------------------------------

class Framer:
    """Accumulates stream bytes, yields complete lines. Resynchronizes
    after garbage because every message ends with \\n."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        lines = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            line = bytes(self._buf[:i])
            del self._buf[: i + 1]
            if line.strip():
                lines.append(line)
        return lines


class Dedup:
    """Drops duplicate ids and replayed/out-of-order seq per (boot, sender)."""

    def __init__(self, cap: int = 2000) -> None:
        self._seen: set[str] = set()
        self._order: list[str] = []
        self._cap = cap
        self._last_seq: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def check(self, fields: dict) -> bool:
        """True when the frame is new (records it); False to drop."""
        mid = fields.get("id")
        key = (fields.get("boot", ""), fields.get("from", ""))
        seq = fields.get("seq")
        with self._lock:
            if not isinstance(mid, str) or mid in self._seen:
                return False
            if isinstance(seq, int) and seq <= self._last_seq.get(key, -1):
                return False
            self._seen.add(mid)
            self._order.append(mid)
            if len(self._order) > 3000:
                self._seen.discard(self._order.pop(0))
            if isinstance(seq, int):
                self._last_seq[key] = seq
            return True
