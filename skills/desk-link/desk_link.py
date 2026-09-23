#!/usr/bin/env python3
"""Desk side of Bonsai-Geronimo-Branc.

Stdlib only. Runs the DualLink daemon (direct TLS listener + ntfy relay,
automatic failover both directions) and hosts a tiny local web frontend
for the human at the desk (the "host").

Two interfaces, one link:

1. Command Code bus (stdin/stdout):
     stdin  line of plain text -> send_chat() over the best transport
     stdout DESKLINK-CHAT {"from": "...", "text": "..."} per inbound frame
   Status and errors go to stderr; stdout is reserved for chat frames.
   Text pipe only, never a shell. Inbound text is never executed.

2. Host frontend (browser client, http://127.0.0.1:<frontend_port>/):
     chat view + transport status + send box. Binds 127.0.0.1 only --
     deliberately NOT reachable from the network. The browser page is
     the "client side".

Transports (mirror of juno_relay.py):
  * direct: TLS *listener* on 0.0.0.0:<direct_port> with the desk's
            self-signed cert. Juno connects out (pinned cert + mutual
            PSK nonce/proof handshake per connection).
  * ntfy  : public relay. Subscribe = GET <base>/<ntfy_outbox>/sse ;
            publish = POST <base>/<ntfy_inbox>. (Outbox/inbox are named
            from Juno's perspective; the desk mirrors them.)

Usage:
    python desk_link.py [--config config.json] [--frontend-port 17432]

Prereqs: wire.py alongside this file, desk.key/desk.crt generated,
config.json with the real secrets (see BONSAI-GERONIMO-BRANC.md).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets as _secrets
import socket
import ssl
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wire  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    print(f"[desk-link] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# ntfy transport (desk side: subscribe outbox, publish inbox)
# --------------------------------------------------------------------------

class DeskNtfyTransport(wire.Transport):
    name = "ntfy"

    def __init__(self, link_holder, base: str, inbox: str, outbox: str) -> None:
        self._link = link_holder
        self.base = base.rstrip("/")
        self.inbox = inbox      # desk -> juno (publish here)
        self.outbox = outbox    # juno -> desk (subscribe here)
        self._up = False
        self._framer = wire.Framer()
        threading.Thread(target=self._subscribe_loop, daemon=True).start()

    @property
    def up(self) -> bool:
        return self._up

    def _subscribe_loop(self) -> None:
        url = f"{self.base}/{self.outbox}/sse"
        backoff = 5.0
        while True:
            try:
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=90) as resp:
                    self._up = True
                    log("ntfy: subscribed")
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
                log(f"ntfy: subscribe failed ({exc}); retry in {backoff:.0f}s")
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
        link = self._link()
        if link is None:
            return
        for raw in self._framer.feed(body.encode("utf-8") + b"\n"):
            link.inbound(raw, self.name)

    def send_bytes(self, data: bytes) -> None:
        req = urllib.request.Request(
            f"{self.base}/{self.inbox}",
            data=data,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201, 202):
                raise IOError(f"ntfy publish -> HTTP {resp.status}")


# --------------------------------------------------------------------------
# direct transport: TLS listener; Juno dials in
# --------------------------------------------------------------------------

class DeskDirectListener(wire.Transport):
    """TLS server. Each connection does the mutual PSK handshake:

        juno -> desk: HMAC'd envelope {type:"hello", nonce: Nc}
        desk -> juno: {"type":"welcome", "proof": proof(PSK,Nc), "nonce": Ns}
        juno -> desk: HMAC'd envelope {type:"ready", proof: proof(PSK,Ns)}

    then newline-delimited envelopes flow both ways.
    """

    name = "direct"

    def __init__(self, link_holder, psk_hex: str, port: int,
                 certfile: str, keyfile: str) -> None:
        self._link = link_holder
        self.psk = psk_hex
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        self._up = False
        self._sock = None
        self._lock = threading.Lock()
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
        srv.listen(5)
        self._up = True
        log(f"direct: listening on 0.0.0.0:{self.port}")
        while True:
            try:
                raw, addr = srv.accept()
                threading.Thread(target=self._handle_conn,
                                 args=(raw, addr), daemon=True).start()
            except Exception as exc:  # noqa: BLE001
                log(f"direct: accept failed ({exc})")
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
        try:
            sock = self._ctx().wrap_socket(raw, server_side=True)
        except Exception as exc:  # noqa: BLE001
            log(f"direct: TLS wrap failed from {addr[0]} ({exc})")
            raw.close()
            return
        try:
            framer = wire.Framer()
            # 1. hello
            raw_hello = self._readline(sock, framer)
            if raw_hello is None:
                raise IOError("no hello")
            try:
                hello = wire.verify_envelope(self.psk,
                                             json.loads(raw_hello.decode("utf-8")))
            except Exception:
                hello = None
            if not hello or hello.get("type") != "hello":
                raise IOError("bad hello")
            nonce_c = hello.get("nonce", "")
            # 2. welcome
            nonce_s = _secrets.token_hex(16)
            welcome = {"type": "welcome",
                       "proof": wire.proof(self.psk, nonce_c),
                       "nonce": nonce_s}
            sock.sendall(json.dumps(welcome).encode() + b"\n")
            # 3. ready
            raw_ready = self._readline(sock, framer)
            if raw_ready is None:
                raise IOError("no ready")
            try:
                ready = wire.verify_envelope(self.psk,
                                             json.loads(raw_ready.decode("utf-8")))
            except Exception:
                ready = None
            if (not ready or ready.get("type") != "ready"
                    or ready.get("proof") != wire.proof(self.psk, nonce_s)):
                raise IOError("ready failed PSK proof — aborting")
            sock.settimeout(None)
            with self._lock:
                self._sock = sock
            log(f"direct: peer {addr[0]} handshake OK")
            link = self._link()
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    raise IOError("peer closed")
                if link is None:
                    link = self._link()
                for raw_line in framer.feed(chunk):
                    if link is not None:
                        link.inbound(raw_line, self.name)
        except Exception as exc:  # noqa: BLE001
            log(f"direct: conn {addr[0]} ended ({exc})")
        finally:
            with self._lock:
                if self._sock is sock:
                    self._sock = None
            try:
                sock.close()
            except Exception:
                pass

    def send_bytes(self, data: bytes) -> None:
        with self._lock:
            sock = self._sock
        if sock is None:
            raise IOError("direct: no peer connected")
        sock.sendall(data)


# --------------------------------------------------------------------------
# host frontend (browser client on 127.0.0.1)
# --------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bonsai-Geronimo-Branc</title>
<style>
:root{--mag:#e0459a;--bg:#0d0b12;--panel:#16121f;--ink:#e8e2f2;--dim:#9a8fb5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif;display:flex;
flex-direction:column;height:100vh}
header{padding:10px 14px;background:var(--panel);border-bottom:2px solid var(--mag);
display:flex;align-items:center;gap:10px}
header h1{font-size:16px;margin:0;font-weight:700}
header h1 span{color:var(--mag)}
#status{margin-left:auto;display:flex;gap:12px;font-size:13px;color:var(--dim)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;
vertical-align:baseline;background:#555}
.dot.on{background:#3ddc84}.dot.off{background:#e05252}
#log{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:78%;padding:8px 12px;border-radius:12px;background:#221c33;overflow-wrap:anywhere}
.msg.me{align-self:flex-end;background:#3a1445;border:1px solid var(--mag)}
.msg .who{font-size:11px;color:var(--dim);margin-bottom:2px}
.msg .via{font-size:10px;color:var(--dim);opacity:.7;margin-top:3px}
form{display:flex;gap:8px;padding:10px 12px;background:var(--panel);
border-top:1px solid #2c2540}
input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid #2c2540;
background:#0d0b12;color:var(--ink);font-size:15px}
button{padding:10px 18px;border-radius:10px;border:0;background:var(--mag);
color:#fff;font-weight:700;cursor:pointer}
</style></head><body>
<header><h1>BONSAI<span>-</span>GERONIMO<span>-</span>BRANC</h1>
<div id="status"><span id="st0"></span><span id="st1"></span></div></header>
<div id="log"></div>
<form id="f"><input id="t" autocomplete="off" placeholder="Type to Juno&#8230;">
<button>Send</button></form>
<script>
let lastN=0;const log=document.getElementById('log');
function esc(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;',
'>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function tick(){fetch('/api/status').then(r=>r.json()).then(s=>{
 (s.transports||[]).forEach((t,i)=>{const el=document.getElementById('st'+i);
  if(el)el.innerHTML='<span class="dot '+(t.live?'on':'off')+'"></span>'+
   esc(t.name)+(t.name===s.primary?' (primary)':'');});}).catch(()=>{});
 fetch('/api/messages?since='+lastN).then(r=>r.json()).then(d=>{
  (d.messages||[]).forEach(m=>{lastN=Math.max(lastN,m.n);
   const div=document.createElement('div');
   div.className='msg'+(m.from==='desk'?' me':'');
   div.innerHTML='<div class="who">'+esc(m.from)+'</div><div>'+
    esc(m.text)+'</div>'+(m.via?'<div class="via">via '+esc(m.via)+'</div>':'');
   log.appendChild(div);});
  if(d.messages.length)log.scrollTop=log.scrollHeight;}).catch(()=>{});}
document.getElementById('f').onsubmit=e=>{e.preventDefault();
 const t=document.getElementById('t');const v=t.value.trim();if(!v)return;
 fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({text:v})});t.value='';};
setInterval(tick,1000);tick();
</script></body></html>
"""


class Frontend:
    """Local host UI: message store + HTTP server on 127.0.0.1."""

    def __init__(self):
        self._lock = threading.Lock()
        self._messages: list = []
        self._counter = 0
        self._link = None

    def attach(self, link: wire.DualLink):
        self._link = link

    def add(self, frm: str, text: str, via: str | None = None):
        with self._lock:
            self._counter += 1
            self._messages.append({
                "n": self._counter, "from": frm, "text": text,
                "ts": int(time.time()), "via": via,
            })
            if len(self._messages) > 300:
                del self._messages[:100]

    def messages_since(self, n: int) -> list:
        with self._lock:
            return [m for m in self._messages if m["n"] > n]

    def serve(self, port: int):
        frontend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep daemon logs clean
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    body = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/status":
                    self._json(frontend._link.status() if frontend._link else {})
                elif parsed.path == "/api/messages":
                    try:
                        since = int(parse_qs(parsed.query).get("since", ["0"])[0])
                    except ValueError:
                        since = 0
                    self._json({"messages": frontend.messages_since(since)})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if urlparse(self.path).path != "/api/send":
                    return self._json({"error": "not found"}, 404)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(length) or b"{}")
                    text = str(data.get("text", "")).strip()
                except Exception:
                    return self._json({"error": "bad request"}, 400)
                if not text:
                    return self._json({"error": "empty"}, 400)
                if frontend._link is None:
                    return self._json({"error": "link not ready"}, 503)
                try:
                    via = frontend._link.send_chat(text)
                except Exception as exc:  # noqa: BLE001
                    return self._json({"error": str(exc)}, 502)
                frontend.add("desk", text, via)
                self._json({"ok": True, "via": via})

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Desk side of Bonsai-Geronimo-Branc")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--frontend-port", type=int, default=17432)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    psk = cfg["psk"]
    holder: dict = {"link": None}

    direct = DeskDirectListener(
        lambda: holder["link"], psk,
        int(cfg.get("direct_port", 17431)),
        cfg.get("tls_certfile", os.path.join(HERE, "desk.crt")),
        cfg.get("tls_keyfile", os.path.join(HERE, "desk.key")),
    )
    ntfy = DeskNtfyTransport(
        lambda: holder["link"],
        cfg.get("ntfy_base", "https://ntfy.sh"),
        cfg["ntfy_inbox"], cfg["ntfy_outbox"],
    )

    frontend = Frontend()

    def on_chat(frm: str, text: str) -> None:
        frontend.add(frm, text)
        payload = json.dumps({"from": frm, "text": text}, ensure_ascii=False)
        print(f"DESKLINK-CHAT {payload}", flush=True)

    # Juno dials out over direct; the desk listens. Primary = direct.
    link = wire.DualLink(psk, "desk", on_chat, direct, ntfy)
    holder["link"] = link
    frontend.attach(link)
    link.start()

    frontend.serve(args.frontend_port)
    log(f"host frontend at http://127.0.0.1:{args.frontend_port}/")
    log("daemon up. stdin lines -> juno | stdout DESKLINK-CHAT <- juno")

    try:
        for line in sys.stdin:
            text = line.rstrip("\n")
            if not text:
                continue
            try:
                via = link.send_chat(text)
                frontend.add("desk", text, via)
                log(f"sent via {via}: {text[:60]}")
            except Exception as exc:  # noqa: BLE001
                log(f"send failed: {exc}")
    except KeyboardInterrupt:
        pass
    finally:
        link.stop()


if __name__ == "__main__":
    main()
