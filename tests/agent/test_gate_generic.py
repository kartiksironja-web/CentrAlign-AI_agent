"""The gate has no built-in knowledge of any company: a made-up system works with zero code changes."""
import json

from agent.connectors import load_connectors
from agent.gate import Gate, GateRequest
from agent.policy import load_policy

HOST = "http://10.0.0.5:7000"


def build(tmp_path, fetch=None):
    (tmp_path / "policy.yaml").write_text(
        """
default_host_policy: deny
rules:
  - {id: big_note, action: file_note, when: {field: size, op: ">", value: 10}, effect: require_approval}
  - {id: no_peeking, match: {path_glob: "/secret/*"}, effect: deny, message: "restricted area"}
  - {id: no_shred, action: shred_note, effect: deny, message: "shredding is not allowed"}
"""
    )
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "ledger.yaml").write_text(
        f"""
id: ledger
name: Note ledger
base_url: {HOST}
description: A made-up system that keeps notes.
actions:
  - name: file_note
    match: [{{method: POST, path: /notes}}]
    extract: {{size: "json.size", title: "json.title"}}
    approval_key: [title, size]
  - name: shred_note
    match: [{{method: POST, path: "/notes/{{id}}/shred"}}]
    extract: {{note_id: "path.id"}}
"""
    )
    return Gate(load_policy(tmp_path / "policy.yaml"), load_connectors(tmp_path / "connectors"),
                fetch=fetch or (lambda url: (500, "")))


def post(path, obj):
    return GateRequest("POST", HOST + path, json.dumps(obj), "application/json")


def test_actions_rules_and_approvals_work_for_a_system_the_code_has_never_heard_of(tmp_path):
    gate = build(tmp_path)
    assert gate.evaluate(post("/notes", {"title": "a", "size": 3})).allowed
    big = gate.evaluate(post("/notes", {"title": "b", "size": 30}))
    assert (big.effect, big.rule_id) == ("require_approval", "big_note")
    gate.approvals.decide(big.key, True)
    assert gate.evaluate(post("/notes", {"title": "b", "size": 30})).allowed


def test_deny_rules_and_unmapped_writes_and_hosts_work_the_same_way(tmp_path):
    gate = build(tmp_path)
    assert gate.evaluate(GateRequest("GET", HOST + "/secret/plans")).rule_id == "no_peeking"
    assert gate.evaluate(GateRequest("GET", HOST + "/notes")).allowed
    assert gate.evaluate(post("/other", {})).rule_id == "unmapped_write"
    assert gate.evaluate(GateRequest("GET", "http://10.0.0.6:7000/notes")).rule_id == "host_policy"
    shred = gate.evaluate(GateRequest("POST", HOST + "/notes/4/shred"))
    assert (shred.effect, shred.rule_id) == ("deny", "no_shred") and "not allowed" in shred.message
