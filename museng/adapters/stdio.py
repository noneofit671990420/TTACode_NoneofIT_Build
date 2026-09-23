"""Stdio bus: the Command Code-style machine interface.

    inbound  (party -> harness): printed as  MUSENG-CHAT {"from","text"}
                                  and        MUSENG-TASK {"task_id","kind","spec","from"}
    outbound (harness -> party): write a plain text line on stdin to chat;
                                  write  TASK <json>  to propose a task.

Outbound task JSON: {"to": "<peer>|*", "kind": "...", "spec": {...}}.
Inbound task results print as MUSENG-RESULT {...}.

Text pipe only, never a shell.
"""

from __future__ import annotations

import json
import sys
import threading


class StdioBus:
    def __init__(self, on_chat, on_task_propose):
        self.on_chat = on_chat            # fn(text)
        self.on_task_propose = on_task_propose  # fn(to, kind, spec)

    def emit_chat(self, frm: str, text: str) -> None:
        print("MUSENG-CHAT " + json.dumps({"from": frm, "text": text},
                                          ensure_ascii=False), flush=True)

    def emit_task(self, task_id: str, kind: str, spec: dict, frm: str) -> None:
        print("MUSENG-TASK " + json.dumps(
            {"task_id": task_id, "kind": kind, "spec": spec, "from": frm},
            ensure_ascii=False), flush=True)

    def emit_result(self, payload: dict) -> None:
        print("MUSENG-RESULT " + json.dumps(payload, ensure_ascii=False),
              flush=True)

    def loop(self) -> None:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("TASK "):
                try:
                    req = json.loads(line[5:])
                    self.on_task_propose(req.get("to", "*"),
                                         req["kind"], req.get("spec", {}))
                except Exception as exc:  # noqa: BLE001
                    print(f"[museng] bad TASK line: {exc}",
                          file=sys.stderr, flush=True)
            else:
                self.on_chat(line)

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True).start()
