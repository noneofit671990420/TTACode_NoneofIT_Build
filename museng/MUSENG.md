# Mus.eng — the party protocol

> **One-line pitch:** a safe, tiny protocol so *any* local AI can
> communicate — with your local AI as the gatekeeper for anything that
> touches the machine.

Mus.eng grows out of the Bonsai-Geronimo-Branc desk link (see
`skills/desk-link/`, the frozen point-to-point v1). The wire security
contract is unchanged; Mus.eng adds addressing, parties, and the
gatekeeper task flow.

## The safety story

1. **Every frame is HMAC-SHA256 authenticated** with a shared 256-bit
   PSK. Flip one bit after authentication and the frame is dropped.
   Replays and duplicates are rejected (per-sender boot id + monotonic
   seq + message-id dedup).
2. **Text pipe only, by default.** Inbound chat text is never executed.
3. **Nothing runs without the gatekeeper.** A remote peer (Muse,
   another AI) can only *propose* work via `task-propose`. The local AI
   — or the human at the frontend — approves or denies. Approved tasks
   run jailed inside `work_root`, with a command allowlist, a timeout,
   and truncated output. `..` escapes and non-allowlisted commands are
   refused even after approval.
4. **Transports are untrusted.** ntfy.sh is a public relay; the direct
   socket is TLS with a pinned self-signed cert plus a mutual PSK
   handshake per connection. Compromise either pipe and you still can't
   forge a frame.

## Pieces

| File | What it is |
|---|---|
| `wire.py` | Envelopes, HMAC, framing, dedup. `to` = peer name or `"*"` (the party). |
| `party.py` | `PartyHub`: verify → dedup → route by `to` → dispatch. `PartyDirectListener`: TLS listener for many peers. `NtfyBus`: the relay as one logical peer. |
| `gatekeeper.py` | `Task` / `Policy` / `Gatekeeper` / jailed `execute()` / `LocalAIDecider`. |
| `adapters/ollama.py` | Any `ollama run <model>` becomes a party member. |
| `adapters/openai_compat.py` | llama-server, vLLM, LM Studio, anything OpenAI-compatible. |
| `adapters/stdio.py` | The Command Code-style machine bus (`MUSENG-*` lines, `TASK <json>` out). |
| `host.py` | The desk-side party daemon: hub + transports + gatekeeper + party bots + localhost frontend. |
| `client.py` | Juno-side party client (direct + ntfy, stdio bus). |

## Frame types

`chat {text}` · `ping/pong` · `hello/welcome/ready` (direct handshake) ·
`announce {peer, capabilities}` · `task-propose {task_id, kind, spec}` ·
`task-decision {task_id, approved, reason}` ·
`task-result {task_id, ok, output, reason?}`

Task kinds: `write-file {path, content}` · `read-file {path}` ·
`list-dir {path}` · `run {cmd:[...], cwd?}`.

## Quickstart — host the party (desk)

```powershell
cd museng
copy config.example.json config.json   # then fill in psk + topics
openssl req -x509 -newkey rsa:2048 -keyout desk.key -out desk.crt -days 825 -nodes -subj "/CN=desk-link"
# forward TCP 17431 on the router to this PC
python -m museng.host
```

Open `http://127.0.0.1:17432/` — chat, peer list, and any proposed tasks
waiting for your Approve/Deny.

Add party bots in `config.json` (`party_bots`): Lucy on Ollama, a GGUF
brain on llama-server — any local AI can communicate.

Set `"gatekeeper": {"ai": "lucy"}` and Lucy rules on task proposals
herself instead of the human.

## Quickstart — join the party (Juno)

```bash
python museng/client.py   # MUSENG_CONFIG=path/to/config.json
```

Then on stdin: a plain line chats to the party, `CHAT lucy hey` addresses
Lucy, and

```
TASK {"to":"desk","kind":"write-file","spec":{"path":"hello.txt","content":"hi"}}
```

proposes work. Watch stdout for `MUSENG-TASK-DECISION` /
`MUSENG-TASK-RESULT`.

## Packaging

Stdlib only — no dependencies, ever. Install options:

```bash
pip install .                 # from the repo root of TTACode (museng/)
python -m museng.host         # run the party host
```

`pyproject.toml` exposes a `museng-host` console script.

## What "the parties" means

The old link was two cans and a string. Mus.eng is the room: Juno,
Command Code, Lucy, a GGUF brain on llama-server — all in one
conversation, addressed or broadcast, over whatever transport is alive.
Invite everyone.
