"""Shared wire protocol for the Bonsai-Geronimo-Branc desk link.

Stdlib only. Both ends (juno_relay.py, desk_link.py) import this module,
so the security contract lives in exactly one place.

Framing
-------
UTF-8 JSON, one object per line, ``\\n``-terminated. This is the "ASCII
ENTER" framing: every statement ends with ENTER, so a stream can always
be resynchronized after garbage.

Integrity ("no 1bit compromise after auth")
------------------------------------------
Every envelope carries ``hmac`` = HMAC-SHA256(PSK, canonical JSON of all
other fields). Canonical JSON = sort_keys, no whitespace. A single
flipped bit anywhere in the message breaks the MAC and the frame is
dropped before it is ever dispatched. Verification uses compare_digest.

Each envelope also carries ``boot`` (random per process start) and a
monotonic ``seq`` per sender. The receiver tracks last seq per
(boot, sender) and drops replays and duplicates (by ``id`` too), so a
captured message can never be replayed into the stream later.

Transports are untrusted individually: ntfy.sh is a public relay (topics
are capability secrets, TLS in transit) and the direct socket is TLS with
a pinned self-signed cert plus a PSK handshake. Either transport can be
fully compromised without forging a message — the HMAC is the auth, not
the pipe.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import threading
import time
import uuid

VERSION = 0


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
    type_: str = "chat",
    text: str | None = None,
    extra: dict | None = None,
) -> dict:
    fields: dict = {
        "v": VERSION,
        "type": type_,
        "from": sender,
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
            if len(self._order) > self._cap:
                self._seen.discard(self._order.pop(0))
            if isinstance(seq, int):
                self._last_seq[key] = seq
            return True


# --------------------------------------------------------------------------
# Dual-transport link with bidirectional failover
# --------------------------------------------------------------------------

class Transport:
    """Interface every transport implements."""

    name = "?"

    def send_bytes(self, data: bytes) -> None:
        raise NotImplementedError

    @property
    def up(self) -> bool:  # noqa: D102
        raise NotImplementedError


class DualLink:
    """Two transports, one logical link. Sends prefer the primary and
    fail over to the secondary on any error; each transport heals itself
    and the link fails back automatically. Both directions use the same
    selection, so failover works symmetrically.

    Inbound frames are verified (HMAC), deduplicated, replay-checked, then
    dispatched. ``on_chat(from, text)`` receives chat frames.
    """

    def __init__(
        self,
        psk_hex: str,
        me: str,
        on_chat,
        primary: Transport,
        secondary: Transport,
        heartbeat_s: float = 30.0,
        stale_s: float = 120.0,
    ) -> None:
        self.psk = psk_hex
        self.me = me
        self.on_chat = on_chat
        self.primary = primary
        self.secondary = secondary
        self.boot = make_boot_id()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._dedup = Dedup()
        self._last_rx: dict[str, float] = {}
        self._send_lock = threading.Lock()
        self._heartbeat_s = heartbeat_s
        self._stale_s = stale_s
        self._stop = threading.Event()

    # -- outbound --------------------------------------------------------

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _send_on(self, transport: Transport, fields: dict) -> None:
        transport.send_bytes(encode_line(fields))

    def _send_fields(self, fields: dict, via: str | None = None) -> str:
        """Send on ``via`` transport, or auto-select. Returns transport name."""
        order = [self.primary, self.secondary]
        if via is not None:
            order = [t for t in order if t.name == via] or order
        else:
            # Prefer primary unless it looks stale.
            if not self._transport_live(self.primary):
                order = [self.secondary, self.primary]
        last_err: Exception | None = None
        for t in order:
            try:
                with self._send_lock:
                    self._send_on(t, fields)
                return t.name
            except Exception as exc:  # transport failed: fail over
                last_err = exc
        raise IOError(f"all transports down: {last_err}")

    def _transport_live(self, t: Transport) -> bool:
        if not t.up:
            return False
        rx = self._last_rx.get(t.name)
        if rx is not None and time.time() - rx > self._stale_s:
            return False
        return True

    def send_chat(self, text: str) -> str:
        fields = build_envelope(
            self.psk, sender=self.me, boot=self.boot,
            seq=self._next_seq(), type_="chat", text=text,
        )
        return self._send_fields(fields)

    def send_ping(self, via: str | None = None) -> str:
        fields = build_envelope(
            self.psk, sender=self.me, boot=self.boot,
            seq=self._next_seq(), type_="ping",
            extra={"ping_id": uuid.uuid4().hex[:8]},
        )
        return self._send_fields(fields, via=via)

    # -- inbound ---------------------------------------------------------

    def inbound(self, raw: bytes, tname: str) -> None:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        fields = verify_envelope(self.psk, obj)
        if fields is None:
            return  # tampered or foreign: drop silently
        if not self._dedup.check(fields):
            return  # duplicate / replay: drop
        self._last_rx[tname] = time.time()
        ftype = fields.get("type")
        if ftype == "chat" and fields.get("from") != self.me:
            self.on_chat(fields.get("from", "?"), fields.get("text", ""))
        elif ftype == "ping" and fields.get("from") != self.me:
            pong = build_envelope(
                self.psk, sender=self.me, boot=self.boot,
                seq=self._next_seq(), type_="pong",
                extra={"ping_id": fields.get("ping_id")},
            )
            try:
                self._send_fields(pong, via=tname)
            except Exception:
                pass
        # "pong" frames just refresh _last_rx; nothing else to do.

    def status(self) -> dict:
        """Snapshot for frontends: which transport is primary, live state,
        and seconds since last verified inbound frame per transport."""

        def tstat(t: Transport) -> dict:
            rx = self._last_rx.get(t.name)
            return {
                "name": t.name,
                "live": self._transport_live(t),
                "connected": bool(t.up),
                "last_rx_s": round(time.time() - rx, 1) if rx else None,
            }

        return {
            "me": self.me,
            "primary": self.primary.name,
            "transports": [tstat(self.primary), tstat(self.secondary)],
        }

    # -- background ------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_s):
            for t in (self.primary, self.secondary):
                if t.up:
                    try:
                        self.send_ping(via=t.name)
                    except Exception:
                        pass

    def stop(self) -> None:
        self._stop.set()
