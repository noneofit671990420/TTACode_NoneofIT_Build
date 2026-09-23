"""Mus.eng party hub: the room everyone is invited to.

Peers (local AIs via adapters, remote Muses over the wire, the human via
the frontend) join by name. Frames carry "to": a peer name or "*" for the
whole party. The hub verifies every frame (HMAC), drops replays, and
routes:

  * to == "*"      -> the party: local dispatch + forward to all peers
                     except the sender
  * to == me       -> local dispatch only
  * to == <peer>   -> that peer only
  * anything else  -> dropped

Dedup is per-hub, so a forwarded frame that loops back is dropped, not
re-broadcast: the party can't echo itself to death.

Transports plug in underneath:
  * PartyDirectListener — TLS listener accepting MANY peers; each
    connection binds to the sender of its hello envelope after the
    mutual PSK handshake.
  * NtfyBus — the public relay as one logical peer ("ntfy").
"""

from __future__ import annotations

import json
import secrets as _secrets
import socket
import ssl
import threading
import time
import urllib.request

from . import wire
from .wire import PARTY


class PartyHub:
    def __init__(self, psk_hex: str, me: str, on_chat=None,
                 on_task_propose=None, on_announce=None,
                 heartbeat_s: float = 30.0, stale_s: float = 120.0):
        self.psk = psk_hex
        self.me = me
        self.on_chat = on_chat                    # fn(frm, to, text)
        self.on_task_propose = on_task_propose    # fn(frm, to, task_id, kind, spec)
        self.on_announce = on_announce            # fn(frm, capabilities)
        self.boot = wire.make_boot_id()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._dedup = wire.Dedup()
        self._last_rx: dict[str, float] = {}
        self._peers: dict[str, object] = {}       # name -> send_bytes callable
        self._peer_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._heartbeat_s = heartbeat_s
        self._stale_s = stale_s
        self._stop = threading.Event()

    # -- peers ---------------------------------------------------------

    def add_peer(self, name: str, send_bytes) -> None:
        if name and name != self.me:
            with self._peer_lock:
                self._peers[name] = send_bytes

    def remove_peer(self, name: str) -> None:
        with self._peer_lock:
            self._peers.pop(name, None)

    def peers(self) -> list[str]:
        with self._peer_lock:
            return sorted(self._peers)

    # -- outbound ------------------------------------------------------

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _targets(self, to: str, sender: str) -> list:
        with self._peer_lock:
            if to == PARTY:
                return [fn for name, fn in self._peers.items()
                        if name != sender]
            fn = self._peers.get(to)
            return [fn] if fn and to != sender else []

    def _relay_raw(self, raw: bytes, to: str, sender: str) -> None:
        """Forward a frame byte-for-byte, preserving the sender's HMAC."""
        data = raw if raw.endswith(b"\n") else raw + b"\n"
        for fn in self._targets(to, sender):
            try:
                with self._send_lock:
                    fn(data)
            except Exception:
                pass

    def _send_frame(self, fields: dict, sender: str) -> None:
        data = wire.encode_line(fields)
        for fn in self._targets(fields.get("to", PARTY), sender):
            try:
                with self._send_lock:
                    fn(data)
            except Exception:
                pass

    def send_chat(self, text: str, to: str = PARTY) -> None:
        self.send_chat_as(self.me, text, to)

    def send_chat_as(self, sender: str, text: str, to: str = PARTY) -> None:
        """Send chat under another party member's name (local AI bots)."""
        fields = wire.build_envelope(
            self.psk, sender=sender, boot=self.boot,
            seq=self._next_seq(), to=to, type_="chat", text=text)
        self._send_frame(fields, sender)

    def propose_task(self, to: str, kind: str, spec: dict,
                     task_id: str | None = None) -> str:
        import uuid as _uuid
        tid = task_id or _uuid.uuid4().hex[:12]
        fields = wire.build_envelope(
            self.psk, sender=self.me, boot=self.boot,
            seq=self._next_seq(), to=to, type_="task-propose",
            extra={"task_id": tid, "kind": kind, "spec": spec})
        self._send_frame(fields, self.me)
        return tid

    def send_decision(self, to: str, task_id: str, approved: bool,
                      reason: str = "") -> None:
        fields = wire.build_envelope(
            self.psk, sender=self.me, boot=self.boot,
            seq=self._next_seq(), to=to, type_="task-decision",
            extra={"task_id": task_id, "approved": approved,
                   "reason": reason})
        self._send_frame(fields, self.me)

    def send_result(self, to: str, task_id: str, ok: bool,
                    output: str = "", reason: str = "") -> None:
        fields = wire.build_envelope(
            self.psk, sender=self.me, boot=self.boot,
            seq=self._next_seq(), to=to, type_="task-result",
            extra={"task_id": task_id, "ok": ok,
                   "output": output, "reason": reason})
        self._send_frame(fields, self.me)

    def announce(self, capabilities: list[str] | None = None) -> None:
        fields = wire.build_envelope(
            self.psk, sender=self.me, boot=self.boot,
            seq=self._next_seq(), to=PARTY, type_="announce",
            extra={"capabilities": capabilities or []})
        self._send_frame(fields, self.me)

    # -- inbound -------------------------------------------------------

    def _for_me(self, to: str) -> bool:
        return to in (PARTY, self.me)

    def inbound(self, raw: bytes, tname: str) -> None:
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            return
        fields = wire.verify_envelope(self.psk, obj)
        if fields is None or not self._dedup.check(fields):
            return
        frm = fields.get("from", "?")
        if frm == self.me:
            return  # never dispatch our own echoes
        self._last_rx[tname] = time.time()
        to = fields.get("to", PARTY)
        ftype = fields.get("type")

        # Relay the frame onward byte-for-byte (HMAC intact), then
        # handle local dispatch.
        self._relay_raw(raw, to, frm)
        if to not in (PARTY, self.me):
            return  # addressed to another peer; not for us

        # Local dispatch (to == me or broadcast).
        if ftype == "chat" and self._for_me(to):
            if self.on_chat:
                self.on_chat(frm, to, fields.get("text", ""))
        elif ftype == "task-propose" and self._for_me(to):
            if self.on_task_propose:
                self.on_task_propose(frm, to, fields.get("task_id", ""),
                                     fields.get("kind", ""),
                                     fields.get("spec", {}))
        elif ftype == "announce":
            if self.on_announce:
                self.on_announce(frm, fields.get("capabilities", []))
        elif ftype == "ping":
            pong = wire.build_envelope(
                self.psk, sender=self.me, boot=self.boot,
                seq=self._next_seq(), to=frm, type_="pong",
                extra={"ping_id": fields.get("ping_id")})
            self._send_frame(pong, self.me)
        # pong / task-decision / task-result: observed by whoever asked.

    def status(self) -> dict:
        return {"me": self.me, "peers": self.peers(),
                "party": PARTY}

    # -- background ----------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_s):
            fields = wire.build_envelope(
                self.psk, sender=self.me, boot=self.boot,
                seq=self._next_seq(), to=PARTY, type_="ping",
                extra={"ping_id": wire.make_boot_id()[:8]})
            self._send_frame(fields, self.me)

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------

class PartyDirectListener:
    """TLS listener for MANY peers. Each connection does the mutual PSK
    handshake, then binds to the hello envelope's sender name."""

    name = "direct"

    def __init__(self, hub: PartyHub, port: int,
                 certfile: str, keyfile: str, log=None):
        self.hub = hub
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        self._log = log or (lambda m: None)
        self._up = False
        threading.Thread(target=self._listen_loop, daemon=True).start()

    @property
    def up(self) -> bool:
        return self._up

    def _ctx(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.certfile, self.keyfile)
        return ctx

    def _listen_loop(self) -> None:
        ctx = self._ctx()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(16)
        self._up = True
        self._log(f"direct: listening on 0.0.0.0:{self.port}")
        while True:
            try:
                raw, addr = srv.accept()
                threading.Thread(target=self._handle_conn,
                                 args=(raw, addr), daemon=True).start()
            except Exception as exc:  # noqa: BLE001
                self._log(f"direct: accept failed ({exc})")
                time.sleep(1)

    def _readline(self, sock, framer, deadline_s=25.0):
        sock.settimeout(5.0)
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                return None
            for raw in framer.feed(chunk):
                return raw
        return None

    def _handle_conn(self, raw, addr) -> None:
        peer = None
        try:
            sock = self._ctx().wrap_socket(raw, server_side=True)
        except Exception as exc:  # noqa: BLE001
            self._log(f"direct: TLS wrap failed from {addr[0]} ({exc})")
            raw.close()
            return
        try:
            framer = wire.Framer()
            raw_hello = self._readline(sock, framer)
            hello = None
            if raw_hello is not None:
                try:
                    hello = wire.verify_envelope(
                        self.hub.psk, json.loads(raw_hello.decode("utf-8")))
                except Exception:
                    hello = None
            if not hello or hello.get("type") != "hello":
                raise IOError("bad hello")
            peer = str(hello.get("from", "")) or addr[0]
            nonce_c = hello.get("nonce", "")
            nonce_s = _secrets.token_hex(16)
            sock.sendall(json.dumps(
                {"type": "welcome",
                 "proof": wire.proof(self.hub.psk, nonce_c),
                 "nonce": nonce_s}).encode() + b"\n")
            raw_ready = self._readline(sock, framer)
            ready = None
            if raw_ready is not None:
                try:
                    ready = wire.verify_envelope(
                        self.hub.psk, json.loads(raw_ready.decode("utf-8")))
                except Exception:
                    ready = None
            if (not ready or ready.get("type") != "ready"
                    or ready.get("proof") != wire.proof(self.hub.psk, nonce_s)):
                raise IOError("ready failed PSK proof — aborting")
            sock.settimeout(None)

            def send_bytes(data: bytes, _s=sock):
                _s.sendall(data)

            self.hub.add_peer(peer, send_bytes)
            self._log(f"direct: peer '{peer}' ({addr[0]}) handshake OK")
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    raise IOError("peer closed")
                for raw_line in framer.feed(chunk):
                    self.hub.inbound(raw_line, self.name)
        except Exception as exc:  # noqa: BLE001
            self._log(f"direct: conn {addr[0]} ended ({exc})")
        finally:
            if peer:
                self.hub.remove_peer(peer)
            try:
                sock.close()
            except Exception:
                pass


class NtfyBus:
    """The public relay as one logical party peer."""

    name = "ntfy"

    def __init__(self, hub: PartyHub, base: str, inbox: str, outbox: str,
                 log=None):
        # inbox: this host publishes here; outbox: this host subscribes here
        # (labels mirror the Juno-side naming: -inbox = desk->juno).
        self.hub = hub
        self.base = base.rstrip("/")
        self.inbox = inbox
        self.outbox = outbox
        self._log = log or (lambda m: None)
        self._up = False
        hub.add_peer("ntfy", self.send_bytes)
        threading.Thread(target=self._subscribe_loop, daemon=True).start()

    @property
    def up(self) -> bool:
        return self._up

    def _subscribe_loop(self) -> None:
        url = f"{self.base}/{self.outbox}/sse"
        backoff = 5.0
        while True:
            try:
                req = urllib.request.Request(
                    url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=90) as resp:
                    self._up = True
                    self._log("ntfy: subscribed")
                    backoff = 5.0
                    buf = b""
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            raise IOError("ntfy stream closed")
                        buf += chunk
                        while b"\n\n" in buf:
                            event, buf = buf.split(b"\n\n", 1)
                            self._handle_event(event)
            except Exception as exc:  # noqa: BLE001
                self._up = False
                self._log(f"ntfy: subscribe failed ({exc}); retry in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120.0)

    def _handle_event(self, event: bytes) -> None:
        data = None
        for line in event.split(b"\n"):
            if line.startswith(b"data:"):
                data = line[5:].strip()
        if not data:
            return
        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            return
        body = msg.get("message")
        if not isinstance(body, str) or not body:
            return
        framer = wire.Framer()
        for raw in framer.feed(body.encode("utf-8") + b"\n"):
            self.hub.inbound(raw, self.name)

    def send_bytes(self, data: bytes) -> None:
        req = urllib.request.Request(
            f"{self.base}/{self.inbox}", data=data,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201, 202):
                raise IOError(f"ntfy publish -> HTTP {resp.status}")
