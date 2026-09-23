"""Tests for museng: the safe party protocol."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from museng import wire
from museng.wire import PARTY
from museng.gatekeeper import Gatekeeper, Policy, Task, execute, LocalAIDecider
from museng.party import PartyHub

PSK = "00" * 32


def env(sender="a", to=PARTY, type_="chat", text="hi", seq=1,
        boot="b1", extra=None):
    return wire.build_envelope(PSK, sender=sender, boot=boot, seq=seq,
                               to=to, type_=type_, text=text, extra=extra)


# ---------------------------------------------------------------- wire ---

def test_to_field_roundtrip():
    e = env(to="lucy")
    assert wire.verify_envelope(PSK, e)["to"] == "lucy"


def test_one_bit_flip_rejected():
    e = env(text="hello")
    line = wire.encode_line(e).decode()
    # flip one bit in the text payload
    i = line.index("hello")
    bad = line[:i] + "jello" + line[i + 5:]
    assert wire.verify_envelope(PSK, json.loads(bad)) is None


def test_wrong_psk_rejected():
    e = env()
    assert wire.verify_envelope("ff" * 32, e) is None


def test_framer_resyncs_after_garbage():
    f = wire.Framer()
    e = env()
    lines = f.feed(b"garbage-line\n" + wire.encode_line(e)[:10])
    # garbage surfaces as a junk line (dies at verify), stream continues
    assert lines == [b"garbage-line"]
    assert wire.verify_envelope(PSK, lines[0]) is None
    lines = f.feed(wire.encode_line(e)[10:])
    assert len(lines) == 1
    assert wire.verify_envelope(PSK, json.loads(lines[0])) is not None


# ---------------------------------------------------------- gatekeeper ---

def make_gk(tmp_path, decider=None):
    policy = Policy(work_root=str(tmp_path),
                    allowed_commands=("python", "python3"))
    results = []
    gk = Gatekeeper(policy, decider=decider,
                    on_result=lambda tid, d, r: results.append((tid, d, r)))
    return gk, results


def test_write_file_approved_executes(tmp_path):
    gk, results = make_gk(tmp_path, decider=lambda t: (True, "looks fine"))
    task = gk.submit("juno", "desk", "t1", "write-file",
                     {"path": "sub/hello.txt", "content": "hi party"})
    assert task.status == "done"
    assert (tmp_path / "sub" / "hello.txt").read_text() == "hi party"
    tid, decision, result = results[0]
    assert decision["approved"] is True
    assert result["ok"] is True


def test_deny_executes_nothing(tmp_path):
    gk, results = make_gk(tmp_path, decider=lambda t: (False, "nope"))
    task = gk.submit("juno", "desk", "t2", "write-file",
                     {"path": "x.txt", "content": "hi"})
    assert task.status == "denied"
    assert not (tmp_path / "x.txt").exists()
    tid, decision, result = results[0]
    assert decision["approved"] is False and result is None


def test_path_escape_rejected_even_when_approved(tmp_path):
    gk, results = make_gk(tmp_path, decider=lambda t: (True, "sure"))
    gk.submit("juno", "desk", "t3", "write-file",
              {"path": "../../evil.txt", "content": "x"})
    tid, decision, result = results[0]
    assert result["ok"] is False
    assert "escapes" in result["reason"]


def test_non_allowlisted_command_rejected(tmp_path):
    gk, results = make_gk(tmp_path, decider=lambda t: (True, "sure"))
    gk.submit("juno", "desk", "t4", "run", {"cmd": ["rm", "-rf", "/"]})
    tid, decision, result = results[0]
    assert result["ok"] is False
    assert "allowlist" in result["reason"]


def test_run_works_for_allowlisted(tmp_path):
    gk, results = make_gk(tmp_path, decider=lambda t: (True, "ok"))
    gk.submit("juno", "desk", "t5", "run",
              {"cmd": [sys.executable, "-c", "print(40 + 2)"]})
    tid, decision, result = results[0]
    assert result["ok"] is True and "42" in result["output"]


def test_bad_kind_rejected_at_intake(tmp_path):
    gk, _ = make_gk(tmp_path)
    with pytest.raises(ValueError):
        gk.submit("juno", "desk", "t6", "format-disk", {})


def test_human_mode_parks_until_resolved(tmp_path):
    gk, results = make_gk(tmp_path, decider=None)  # human mode
    task = gk.submit("juno", "desk", "t7", "write-file",
                     {"path": "h.txt", "content": "z"})
    assert task.status == "pending"
    assert [t.task_id for t in gk.pending()] == ["t7"]
    assert not (tmp_path / "h.txt").exists()
    gk.resolve("t7", True, "human said yes")
    assert (tmp_path / "h.txt").read_text() == "z"
    assert results[0][1]["approved"] is True


class FakeAI:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages):
        return self.reply


def test_local_ai_decider_parses_approve():
    d = LocalAIDecider(FakeAI("APPROVE looks harmless"), "lucy")
    ok, reason = d(Task("t", "write-file", {}, "juno", "desk"))
    assert ok is True and "lucy" in reason


def test_local_ai_decider_parses_deny():
    d = LocalAIDecider(FakeAI("DENY touches the network"), "lucy")
    ok, reason = d(Task("t", "run", {}, "juno", "desk"))
    assert ok is False


def test_local_ai_decider_denies_on_error():
    class Boom:
        def complete(self, messages):
            raise IOError("model down")
    d = LocalAIDecider(Boom())
    ok, _ = d(Task("t", "run", {}, "juno", "desk"))
    assert ok is False


# --------------------------------------------------------------- party ---

def make_hub(name="desk"):
    chats, tasks, announces = [], [], []
    sent = {}  # peer -> [bytes]

    def send_to(peer):
        def _send(data: bytes):
            sent.setdefault(peer, []).append(data)
        return _send

    hub = PartyHub(PSK, name,
                   on_chat=lambda f, t, x: chats.append((f, t, x)),
                   on_task_propose=lambda f, t, i, k, s: tasks.append(
                       (f, t, i, k, s)),
                   on_announce=lambda f, c: announces.append((f, c)))
    return hub, chats, tasks, announces, sent, send_to


def feed(hub, fields, tname="direct"):
    hub.inbound(wire.encode_line(fields), tname)


def test_broadcast_reaches_all_but_sender():
    hub, chats, _, _, sent, send_to = make_hub()
    hub.add_peer("juno", send_to("juno"))
    hub.add_peer("lucy", send_to("lucy"))
    feed(hub, env(sender="juno", text="hey all"))
    assert chats == [("juno", PARTY, "hey all")]
    # forwarded to lucy, not echoed to juno
    assert "lucy" in sent and "juno" not in sent
    f = json.loads(sent["lucy"][0].decode())
    assert wire.verify_envelope(PSK, f)["text"] == "hey all"


def test_addressed_goes_only_to_target():
    hub, chats, _, _, sent, send_to = make_hub()
    hub.add_peer("juno", send_to("juno"))
    hub.add_peer("lucy", send_to("lucy"))
    feed(hub, env(sender="juno", to="lucy", text="psst"))
    assert chats == []            # not for local dispatch
    assert "lucy" in sent and "juno" not in sent


def test_addressed_to_me_dispatched_locally():
    hub, chats, _, _, sent, send_to = make_hub()
    hub.add_peer("juno", send_to("juno"))
    feed(hub, env(sender="juno", to="desk", text="for you"))
    assert chats == [("juno", "desk", "for you")]
    assert sent == {}             # not forwarded anywhere


def test_dedup_kills_rebroadcast_loops():
    hub, chats, _, _, sent, send_to = make_hub()
    hub.add_peer("juno", send_to("juno"))
    e = env(sender="juno", text="loop?")
    feed(hub, e)
    feed(hub, e)  # same frame again (as if looped back)
    assert len(chats) == 1


def test_tampered_frame_dropped_silently():
    hub, chats, _, _, sent, send_to = make_hub()
    hub.add_peer("lucy", send_to("lucy"))
    e = env(sender="juno", text="real")
    line = bytearray(wire.encode_line(e))
    line[line.index(b"real")] ^= 0x01  # 1-bit flip
    hub.inbound(bytes(line), "direct")
    assert chats == [] and sent == {}


def test_task_propose_flows_to_gatekeeper_hook():
    hub, chats, tasks, _, _, send_to = make_hub()
    hub.add_peer("juno", send_to("juno"))
    feed(hub, env(sender="juno", to="desk", type_="task-propose",
                  extra={"task_id": "abc", "kind": "run",
                         "spec": {"cmd": ["python", "--version"]}}))
    assert tasks == [("juno", "desk", "abc", "run",
                      {"cmd": ["python", "--version"]})]


def test_hub_send_chat_as_names_the_bot():
    hub, chats, _, _, sent, send_to = make_hub("desk")
    hub.add_peer("juno", send_to("juno"))
    hub.send_chat_as("lucy", "hi juno")
    f = json.loads(sent["juno"][0].decode())
    fields = wire.verify_envelope(PSK, f)
    assert fields["from"] == "lucy" and fields["to"] == PARTY


def test_propose_task_helper():
    hub, _, _, _, sent, send_to = make_hub("juno")
    hub.add_peer("desk", send_to("desk"))
    tid = hub.propose_task("desk", "write-file",
                           {"path": "a.txt", "content": "b"})
    f = json.loads(sent["desk"][0].decode())
    fields = wire.verify_envelope(PSK, f)
    assert fields["type"] == "task-propose"
    assert fields["task_id"] == tid
