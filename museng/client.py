#!/usr/bin/env python3
"""Mus.eng party client — Juno's side of the party.

Joins the party over direct TLS (pinned cert + PSK handshake, the desk
listens) with ntfy as fallback. Party-aware: frames carry "to".

Stdio bus (for the harness driving this client):

  inbound  (party -> here): MUSENG-CHAT {"from","to","text"}
                           MUSENG-TASK-DECISION {...}
                           MUSENG-TASK-RESULT {...}
  outbound (here -> party): a plain text line          -> party chat
                           CHAT <to> <text>            -> addressed chat
                           TASK <json>                 -> task-propose
                             {"to":"<peer>|*","kind":"...","spec":{...}}

Status and errors go to stderr; stdout is reserved for party frames.
Text pipe only, never a shell.
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import socket
import ssl
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from museng import wire  # noqa: E402
from museng.wire import PARTY

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    print(f"[museng] {msg}", file=sys.stderr, flush=True)


class NtfyPeer:
    name = "ntfy"

    def __init__(self, hub, base, inbox, outbox):
        # inbox: remote->here (subscribe); outbox: here->remote (publish)
        self._hub = hub
        self.base = base.rstrip("/")
        self.inbox = inbox
        self.outbox = outbox
        self._up = False
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def up(self):
        return self._up

    def _loop(self):
        url = f"{self.base}/{self.inbox}/sse"
        backoff = 5.0
        while True:
            try:
                req = urllib.request.Request(
                    url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=90) as resp:
                    self._up = True
                    log("ntfy: subscribed")
                    backoff = 5.0
                    buf = b""
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            raise IOError("closed")
                        buf += chunk
                        while b"\n\n" in buf:
                            event, buf = buf.split(b"\n\n", 1)
                            data = None
                            for line in event.split(b"\n"):
                                if line.startswith(b"data:"):
                                    data = line[5:].strip()
                            if not data:
                                continue
                            try:
                                body = json.loads(data.decode())["message"]
                            except Exception:
                                continue
                            if isinstance(body, str) and body:
                                fr = wire.Framer()
                                for raw in fr.feed(body.encode() + b"\n"):
                                    self._hub(raw, self.name)
            except Exception as exc:  # noqa: BLE001
                self._up = False
                log(f"ntfy: {exc}; retry in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120.0)

    def send(self, data: bytes):
        req = urllib.request.Request(
            f"{self.base}/{self.outbox}", data=data,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30):
            pass


class DirectPeer:
    name = "direct"

    def __init__(self, hub, psk_hex, host, port, cert_pem, me):
        self._hub = hub
        self.psk = psk_hex
        self.host = host
        self.port = port
        self.cert_pem = cert_pem
        self.me = me
        self._up = False
        self._sock = None
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def up(self):
        return self._up

    def _loop(self):
        backoff = 5.0
        while True:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_REQUIRED
                ctx.load_verify_locations(cafile=self.cert_pem)
                raw = socket.create_connection((self.host, self.port),
                                               timeout=20)
                sock = ctx.wrap_socket(raw, server_hostname=self.host)
                nonce_c = _secrets.token_hex(16)
                hello = wire.build_envelope(
                    self.psk, sender=self.me, boot="hs", seq=0,
                    type_="hello", extra={"nonce": nonce_c})
                sock.sendall(wire.encode_line(hello))
                fr = wire.Framer()
                sock.settimeout(20)
                welcome = None
                deadline = time.time() + 20
                while time.time() < deadline and welcome is None:
                    for raw_line in fr.feed(sock.recv(4096)):
                        try:
                            obj = json.loads(raw_line.decode())
                        except Exception:
                            continue
                        if obj.get("type") == "welcome":
                            welcome = obj
                            break
                if not welcome or welcome.get("proof") != wire.proof(
                        self.psk, nonce_c):
                    raise IOError("handshake failed")
                ready = wire.build_envelope(
                    self.psk, sender=self.me, boot="hs", seq=0,
                    type_="ready",
                    extra={"proof": wire.proof(self.psk,
                                              welcome.get("nonce", ""))})
                sock.sendall(wire.encode_line(ready))
                sock.settimeout(None)
                with self._lock:
                    self._sock = sock
                self._up = True
                log("direct: connected + handshake OK")
                backoff = 5.0
                fr2 = wire.Framer()
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise IOError("peer closed")
                    for raw_line in fr2.feed(chunk):
                        self._hub(raw_line, self.name)
            except Exception as exc:  # noqa: BLE001
                self._up = False
                with self._lock:
                    self._sock = None
                log(f"direct: {exc}; retry in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120.0)

    def send(self, data: bytes):
        with self._lock:
            sock = self._sock
        if sock is None:
            raise IOError("direct: not connected")
        sock.sendall(data)


def main() -> None:
    cfg_path = os.environ.get("MUSENG_CONFIG",
                              os.path.join(HERE, "config.json"))
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    psk = cfg["psk"]
    me = cfg.get("name", "juno")
    boot = wire.make_boot_id()
    seq = [0]
    seq_lock = threading.Lock()
    peers = []

    def next_seq():
        with seq_lock:
            seq[0] += 1
            return seq[0]

    def emit(kind, payload):
        print(f"{kind} " + json.dumps(payload, ensure_ascii=False),
              flush=True)

    def inbound(raw: bytes, tname: str):
        try:
            obj = json.loads(raw.decode())
        except Exception:
            return
        fields = wire.verify_envelope(psk, obj)
        if fields is None or fields.get("from") == me:
            return
        ftype = fields.get("type")
        if ftype == "chat":
            emit("MUSENG-CHAT", {"from": fields.get("from"),
                                 "to": fields.get("to", PARTY),
                                 "text": fields.get("text", "")})
        elif ftype == "task-decision":
            emit("MUSENG-TASK-DECISION",
                 {"from": fields.get("from"), "task_id": fields.get("task_id"),
                  "approved": fields.get("approved"),
                  "reason": fields.get("reason", "")})
        elif ftype == "task-result":
            emit("MUSENG-TASK-RESULT",
                 {"from": fields.get("from"), "task_id": fields.get("task_id"),
                  "ok": fields.get("ok"), "output": fields.get("output", ""),
                  "reason": fields.get("reason", "")})
        elif ftype == "announce":
            emit("MUSENG-ANNOUNCE",
                 {"from": fields.get("from"),
                  "capabilities": fields.get("capabilities", [])})

    def send_frame(fields):
        data = wire.encode_line(fields)
        # prefer direct, fall back to ntfy
        errs = []
        for peer in sorted(peers, key=lambda p: p.name != "direct"):
            try:
                peer.send(data)
                return peer.name
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc))
        raise IOError(f"all peers down: {errs}")

    def send_chat(text, to=PARTY):
        fields = wire.build_envelope(
            psk, sender=me, boot=boot, seq=next_seq(),
            to=to, type_="chat", text=text)
        return send_frame(fields)

    def send_task(to, kind, spec):
        import uuid as _uuid
        tid = _uuid.uuid4().hex[:12]
        fields = wire.build_envelope(
            psk, sender=me, boot=boot, seq=next_seq(), to=to,
            type_="task-propose",
            extra={"task_id": tid, "kind": kind, "spec": spec})
        send_frame(fields)
        return tid

    ntfy = NtfyPeer(inbound, cfg.get("ntfy_base", "https://ntfy.sh"),
                    cfg["ntfy_inbox"], cfg["ntfy_outbox"])
    peers.append(ntfy)
    if cfg.get("direct_host") and cfg.get("direct_cert_pem"):
        peers.append(DirectPeer(inbound, psk, cfg["direct_host"],
                                int(cfg.get("direct_port", 17431)),
                                cfg["direct_cert_pem"], me))
        log(f"direct: target {cfg['direct_host']}:{cfg.get('direct_port', 17431)}")
    else:
        log("direct: not configured — ntfy only for now")

    # announce ourselves to the party
    def _announce():
        time.sleep(3)
        try:
            fields = wire.build_envelope(
                psk, sender=me, boot=boot, seq=next_seq(), to=PARTY,
                type_="announce",
                extra={"capabilities": ["chat", "task-propose"]})
            send_frame(fields)
        except Exception:
            pass
    threading.Thread(target=_announce, daemon=True).start()

    log("party client up. stdin -> party | stdout MUSENG-* <- party")
    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                if line.startswith("TASK "):
                    req = json.loads(line[5:])
                    tid = send_task(req.get("to", PARTY), req["kind"],
                                    req.get("spec", {}))
                    log(f"task {tid} proposed ({req['kind']})")
                elif line.startswith("CHAT "):
                    _, to, text = line.split(" ", 2)
                    via = send_chat(text, to)
                    log(f"chat -> {to} via {via}")
                else:
                    via = send_chat(line)
                    log(f"chat -> party via {via}")
            except Exception as exc:  # noqa: BLE001
                log(f"send failed: {exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
