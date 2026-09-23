#!/usr/bin/env python3
"""Mus.eng host — the desk-side party daemon.

Brings the whole party together on this box:

  * PartyHub: every frame verified (HMAC), deduped, routed by "to".
  * PartyDirectListener: TLS listener, many peers, mutual PSK handshake.
  * NtfyBus: the public relay as one logical peer.
  * Gatekeeper: remote task-propose frames are NEVER executed directly —
    the local AI (or the human at the frontend) approves first, then the
    task runs jailed in work_root under the command allowlist.
  * Party bots: local AIs (Ollama / llama-server / ...) join as named
    members — e.g. Lucy on qwen3:8b. Any local AI can communicate.
  * Host frontend: browser chat + peer list + task Approve/Deny buttons
    at http://127.0.0.1:<frontend_port>/ (localhost only).
  * StdioBus: the Command Code machine interface (MUSENG-* lines).

Usage:
    python -m museng.host [--config config.json] [--frontend-port 17432]

Config (see config.example.json): psk, ntfy topics, direct_port,
tls_certfile/keyfile, work_root, gatekeeper, party_bots.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from museng import wire  # noqa: E402
from museng.wire import PARTY
from museng.party import PartyHub, PartyDirectListener, NtfyBus
from museng.gatekeeper import Gatekeeper, Policy, LocalAIDecider
from museng.adapters.stdio import StdioBus

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    print(f"[museng] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# local-AI party members
# --------------------------------------------------------------------------

def make_ai(kind: str, cfg: dict):
    if kind == "ollama":
        from museng.adapters.ollama import OllamaAI
        return OllamaAI(cfg["model"],
                        base_url=cfg.get("base_url", "http://127.0.0.1:11434"))
    if kind == "openai-compat":
        from museng.adapters.openai_compat import OpenAICompatAI
        return OpenAICompatAI(cfg["model"],
                              base_url=cfg.get("base_url", "http://127.0.0.1:8080"),
                              api_key=cfg.get("api_key", ""))
    raise ValueError(f"unknown adapter: {kind!r}")


class PartyBot:
    """A local AI sitting at the party under its own name."""

    def __init__(self, name: str, ai, hub: PartyHub,
                 system: str = "", history_n: int = 20):
        self.name = name
        self.ai = ai
        self.hub = hub
        self.system = system or (
            f"You are {name}, a local AI at a party of AIs. Be brief, warm, "
            "and direct. You are talking with other AIs and a human's "
            "assistant over a text channel.")
        self.history: list[dict] = []
        self.history_n = history_n
        self._lock = threading.Lock()

    def hear(self, frm: str, text: str) -> None:
        if frm == self.name:
            return
        with self._lock:
            self.history.append({"role": "user",
                                 "content": f"[{frm}]: {text}"})
            self.history = self.history[-self.history_n:]
            msgs = [{"role": "system", "content": self.system}] + list(
                self.history)
        threading.Thread(target=self._reply, args=(msgs,), daemon=True).start()

    def _reply(self, msgs) -> None:
        try:
            reply = self.ai.complete(msgs)
        except Exception as exc:  # noqa: BLE001
            log(f"bot {self.name}: complete failed ({exc})")
            return
        with self._lock:
            self.history.append({"role": "assistant", "content": reply})
            self.history = self.history[-self.history_n:]
        self.hub.send_chat_as(self.name, reply)
        log(f"bot {self.name}: replied ({len(reply)} chars)")


# --------------------------------------------------------------------------
# host frontend (localhost only)
# --------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mus.eng party</title>
<style>
:root{--mag:#e0459a;--bg:#0d0b12;--panel:#16121f;--ink:#e8e2f2;--dim:#9a8fb5;
--grn:#3ddc84;--red:#e05252}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif;display:flex;height:100vh}
#side{width:230px;background:var(--panel);border-right:1px solid #2c2540;
padding:12px;overflow-y:auto;flex-shrink:0}
#side h3{font-size:12px;color:var(--dim);text-transform:uppercase;
letter-spacing:1px;margin:14px 0 6px}
#side h3:first-child{margin-top:0}
.peer{font-size:13px;padding:3px 0}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;
background:var(--grn)}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
header{padding:10px 14px;background:var(--panel);border-bottom:2px solid var(--mag)}
header h1{font-size:16px;margin:0}header h1 span{color:var(--mag)}
#log{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:80%;padding:8px 12px;border-radius:12px;background:#221c33;overflow-wrap:anywhere}
.msg.me{align-self:flex-end;background:#3a1445;border:1px solid var(--mag)}
.msg .who{font-size:11px;color:var(--dim);margin-bottom:2px}
.msg .to{font-size:10px;color:var(--dim);opacity:.7}
#tasks{padding:8px 14px;display:flex;flex-direction:column;gap:8px;
border-top:1px solid #2c2540;max-height:32vh;overflow-y:auto}
.task{background:#1c1626;border:1px solid #2c2540;border-radius:10px;padding:8px 10px;font-size:13px}
.task .kind{color:var(--mag);font-weight:700}
.task pre{background:#0d0b12;border-radius:6px;padding:6px;overflow-x:auto;
font-size:12px;max-height:120px;overflow-y:auto}
.task .row{display:flex;gap:8px;margin-top:6px}
.task button{padding:6px 14px;border-radius:8px;border:0;font-weight:700;cursor:pointer}
.approve{background:var(--grn);color:#062}.deny{background:var(--red);color:#fff}
form{display:flex;gap:8px;padding:10px 12px;background:var(--panel);border-top:1px solid #2c2540}
input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid #2c2540;
background:#0d0b12;color:var(--ink);font-size:15px}
form button{padding:10px 18px;border-radius:10px;border:0;background:var(--mag);color:#fff;font-weight:700;cursor:pointer}
</style></head><body>
<div id="side"><h3>Party</h3><div id="peers"></div>
<h3>Gatekeeper</h3><div id="gk" style="font-size:13px;color:var(--dim)"></div></div>
<div id="main">
<header><h1>MUS<span>.</span>ENG <span>party</span></h1></header>
<div id="log"></div>
<div id="tasks"></div>
<form id="f"><input id="t" autocomplete="off" placeholder="Say something to the party&#8230;">
<button>Send</button></form>
</div>
<script>
let lastN=0;const log=document.getElementById('log');
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;',
'>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function tick(){
 fetch('/api/status').then(r=>r.json()).then(s=>{
  document.getElementById('peers').innerHTML=
   ['<div class="peer"><span class="dot"></span>'+esc(s.me)+' (you)</div>']
   .concat((s.peers||[]).map(p=>'<div class="peer"><span class="dot"></span>'+esc(p)+'</div>')).join('');
  document.getElementById('gk').textContent='mode: '+(s.gatekeeper||'?');}).catch(()=>{});
 fetch('/api/messages?since='+lastN).then(r=>r.json()).then(d=>{
  (d.messages||[]).forEach(m=>{lastN=Math.max(lastN,m.n);
   const div=document.createElement('div');
   div.className='msg'+(m.from==='desk'?' me':'');
   div.innerHTML='<div class="who">'+esc(m.from)+
    (m.to&&m.to!=='*'?' <span class="to">-&gt; '+esc(m.to)+'</span>':'')+'</div><div>'+
    esc(m.text)+'</div>';
   log.appendChild(div);});
  if(d.messages.length)log.scrollTop=log.scrollHeight;}).catch(()=>{});
 fetch('/api/tasks').then(r=>r.json()).then(d=>{
  const box=document.getElementById('tasks');
  box.innerHTML=(d.tasks||[]).map(t=>
   '<div class="task"><span class="kind">'+esc(t.kind)+'</span> from <b>'+esc(t.from)+
   '</b> <small>'+esc(t.task_id)+'</small><pre>'+esc(JSON.stringify(t.spec,null,1))+'</pre>'+
   '<div class="row"><button class="approve" onclick="decide(\\''+t.task_id+'\\',true)">Approve</button>'+
   '<button class="deny" onclick="decide(\\''+t.task_id+'\\',false)">Deny</button></div></div>').join('');
 }).catch(()=>{});}
function decide(id,ok){fetch('/api/tasks/'+id+'/decision',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({approved:ok})});}
document.getElementById('f').onsubmit=e=>{e.preventDefault();
 const t=document.getElementById('t');const v=t.value.trim();if(!v)return;
 fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({text:v})});t.value='';};
setInterval(tick,1000);tick();
</script></body></html>
"""


class Frontend:
    def __init__(self, hub: PartyHub, gatekeeper: Gatekeeper,
                 gatekeeper_mode: str):
        self.hub = hub
        self.gatekeeper = gatekeeper
        self.gatekeeper_mode = gatekeeper_mode
        self._lock = threading.Lock()
        self._messages: list = []
        self._counter = 0

    def add(self, frm: str, to: str, text: str):
        with self._lock:
            self._counter += 1
            self._messages.append({"n": self._counter, "from": frm,
                                   "to": to, "text": text,
                                   "ts": int(time.time())})
            if len(self._messages) > 300:
                del self._messages[:100]

    def messages_since(self, n: int) -> list:
        with self._lock:
            return [m for m in self._messages if m["n"] > n]

    def serve(self, port: int):
        fe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
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
                    self.send_header("Content-Type",
                                     "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/api/status":
                    st = fe.hub.status()
                    st["gatekeeper"] = fe.gatekeeper_mode
                    self._json(st)
                elif parsed.path == "/api/messages":
                    try:
                        since = int(parse_qs(parsed.query).get("since", ["0"])[0])
                    except ValueError:
                        since = 0
                    self._json({"messages": fe.messages_since(since)})
                elif parsed.path == "/api/tasks":
                    self._json({"tasks": [fe.gatekeeper.describe(t)
                                          for t in fe.gatekeeper.pending()]})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                parsed = urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    return self._json({"error": "bad request"}, 400)
                if parsed.path == "/api/send":
                    text = str(data.get("text", "")).strip()
                    if not text:
                        return self._json({"error": "empty"}, 400)
                    fe.hub.send_chat(text)
                    fe.add("desk", PARTY, text)
                    return self._json({"ok": True})
                if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/decision"):
                    tid = parsed.path.split("/")[3]
                    approved = bool(data.get("approved"))
                    reason = str(data.get("reason", "")) or "human decision"
                    fe.gatekeeper.resolve(tid, approved, reason)
                    return self._json({"ok": True})
                return self._json({"error": "not found"}, 404)

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Mus.eng party host")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--frontend-port", type=int, default=17432)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    psk = cfg["psk"]
    me = cfg.get("name", "desk")
    work_root = cfg.get("work_root", os.path.join(HERE, "party-work"))
    os.makedirs(work_root, exist_ok=True)

    policy = Policy(
        work_root=work_root,
        allowed_commands=tuple(cfg.get("allowed_commands",
                                       ["python", "python3"])),
        max_runtime_s=float(cfg.get("max_runtime_s", 120)),
    )
    gk_mode = cfg.get("gatekeeper", "human")

    frontend_holder: dict = {}
    bus_holder: dict = {}

    # -- hub ----------------------------------------------------------
    def on_chat(frm: str, to: str, text: str):
        fe = frontend_holder.get("fe")
        if fe:
            fe.add(frm, to, text)
        bus = bus_holder.get("bus")
        if bus:
            bus.emit_chat(frm, text)
        for bot in bots:
            if to in (PARTY, bot.name):
                bot.hear(frm, text)

    def on_result(task_id: str, decision: dict, result: dict | None):
        target = result_targets.pop(task_id, PARTY)  # original requester
        hub.send_decision(target, task_id, decision["approved"],
                          decision.get("reason", ""))
        if result is not None:
            hub.send_result(target, task_id, result.get("ok", False),
                            result.get("output", ""),
                            result.get("reason", ""))
        bus = bus_holder.get("bus")
        if bus:
            bus.emit_result({"task_id": task_id,
                             "approved": decision["approved"],
                             "result": result})

    def on_task_propose(frm: str, to: str, task_id: str, kind: str,
                        spec: dict):
        log(f"task-propose {task_id} {kind} from {frm} -> gatekeeper")
        try:
            task = gatekeeper.submit(frm, to, task_id, kind, spec)
        except ValueError as exc:
            log(f"task rejected at intake: {exc}")
            hub.send_decision(frm, task_id, False, str(exc))
            return
        result_targets[task.task_id] = frm
        bus = bus_holder.get("bus")
        if bus:
            bus.emit_task(task.task_id, kind, spec, frm)

    result_targets: dict[str, str] = {}
    hub = PartyHub(psk, me, on_chat=on_chat,
                   on_task_propose=on_task_propose)
    hub.start()

    # -- gatekeeper ---------------------------------------------------
    bots: list[PartyBot] = []
    bot_by_name: dict[str, PartyBot] = {}
    for b in cfg.get("party_bots", []):
        try:
            ai = make_ai(b["adapter"], b)
            bot = PartyBot(b["name"], ai, hub,
                           system=b.get("system", ""),
                           history_n=int(b.get("history_n", 20)))
            bots.append(bot)
            bot_by_name[b["name"]] = bot
            log(f"party bot '{b['name']}' ({b['adapter']}/{b.get('model')}) joined")
        except Exception as exc:  # noqa: BLE001
            log(f"party bot '{b.get('name')}' failed to start: {exc}")

    decider = None
    if isinstance(gk_mode, dict) and gk_mode.get("ai"):
        ai_name = gk_mode["ai"]
        bot = bot_by_name.get(ai_name)
        if bot:
            decider = LocalAIDecider(bot.ai, model_label=ai_name)
            gk_label = f"ai:{ai_name}"
        else:
            gk_label = "human"
            log(f"gatekeeper ai '{ai_name}' not a party bot; human mode")
    elif gk_mode == "human":
        gk_label = "human"
    else:
        # "ai:<name>" string form
        ai_name = str(gk_mode).split("ai:", 1)[1] if str(gk_mode).startswith("ai:") else None
        bot = bot_by_name.get(ai_name) if ai_name else None
        if bot:
            decider = LocalAIDecider(bot.ai, model_label=ai_name)
            gk_label = f"ai:{ai_name}"
        else:
            gk_label = "human"

    gatekeeper = Gatekeeper(policy, decider=decider, on_result=on_result)
    log(f"gatekeeper mode: {gk_label}; work_root={work_root}")

    # -- transports ---------------------------------------------------
    PartyDirectListener(
        hub, int(cfg.get("direct_port", 17431)),
        cfg.get("tls_certfile", os.path.join(HERE, "desk.crt")),
        cfg.get("tls_keyfile", os.path.join(HERE, "desk.key")),
        log=log)
    NtfyBus(hub, cfg.get("ntfy_base", "https://ntfy.sh"),
            cfg["ntfy_inbox"], cfg["ntfy_outbox"], log=log)

    # -- frontend -----------------------------------------------------
    fe = Frontend(hub, gatekeeper, gk_label)
    frontend_holder["fe"] = fe
    fe.serve(args.frontend_port)
    log(f"party frontend at http://127.0.0.1:{args.frontend_port}/")

    # -- stdio bus (Command Code) -------------------------------------
    def bus_chat(text: str):
        hub.send_chat(text)
        fe.add(me, PARTY, text)

    def bus_task(to: str, kind: str, spec: dict):
        tid = hub.propose_task(to, kind, spec)
        log(f"bus proposed task {tid} ({kind}) -> {to}")

    bus = StdioBus(bus_chat, bus_task)
    bus_holder["bus"] = bus

    hub.announce(capabilities=["chat", "task-propose", "gatekeeper"])
    log("party host up. stdin lines -> party | stdout MUSENG-* <- party")

    try:
        bus.loop()  # foreground: stdin drives the bus
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()


if __name__ == "__main__":
    main()
