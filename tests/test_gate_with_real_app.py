"""The gate next to the REAL mock company (no LLM).

Two purposes. First, show that the loopholes the gate closes are real: the app itself
accepts multipart bodies, decodes %61dmin, and has to pick one of two duplicate form values.
Second, run the whole path: gate -> (if allowed) the app -> the auditor's view of the result.
"""
import pytest
from fastapi.testclient import TestClient

from agent.connectors import load_connectors
from agent.gate import Gate, GateRequest
from agent.policy import load_policy
from apps.company.app import create_app
from apps.company.seed import seed_database
from tests.agent.helpers import PACK
from tests.helpers import query

BASE = "http://testserver"
ADMIN = {"X-Admin-Token": "t"}


@pytest.fixture
def world(tmp_path):
    seed_database(tmp_path / "data")
    client = TestClient(create_app(tmp_path / "data", admin_token="t"), follow_redirects=False)

    def fetch(url):  # the gate's own lookup, answered by the real app
        response = client.get(url.removeprefix(BASE))
        return response.status_code, response.text

    gate = Gate(
        load_policy(PACK / "policy.yaml"),
        load_connectors(PACK / "connectors", {"erp": BASE, "portal": BASE}),
        fetch=fetch,
    )
    return client, gate


def send(client, gate, method, path, data=None, json=None, files=None):
    """What the agent's tools will do: ask the gate, forward only if allowed."""
    if json is not None:
        import json as jsonlib
        request = GateRequest(method, BASE + path, jsonlib.dumps(json), "application/json")
    elif files is not None:
        request = GateRequest(method, BASE + path, "multipart", "multipart/form-data; boundary=x")
    else:
        from urllib.parse import urlencode
        request = GateRequest(method, BASE + path, urlencode(data or {}), "application/x-www-form-urlencoded")
    decision = gate.evaluate(request)
    if not decision.allowed:
        return decision, None
    return decision, client.request(method, path, data=data, json=json, files=files)


ZENITH = {"vendor_id": "5", "invoice_no": "INV-3107", "amount": "80000", "due_date": "10/11/2026"}


# ---- the loopholes are real ----------------------------------------------

def test_the_app_accepts_multipart_so_a_gate_that_ignored_it_could_be_bypassed(world):
    client, gate = world
    fields = {k: (None, v) for k, v in ZENITH.items()}
    assert client.post("/erp/bills/new", files=fields).status_code == 303  # the app happily creates the bill
    assert query(client, "SELECT amount FROM erp_bills WHERE invoice_no = 'INV-3107'") == [{"amount": 80000.0}]
    decision, response = send(client, gate, "POST", "/erp/bills/new", files=fields)
    assert (decision.effect, decision.rule_id) == ("deny", "unreadable_request") and response is None


def test_the_app_decodes_percent_encoded_admin_so_the_gate_must_too(world):
    client, gate = world
    assert client.get("/%61dmin/faults", headers=ADMIN).status_code == 200  # the app serves it
    decision, _ = send(client, gate, "GET", "/%61dmin/faults")
    assert decision.rule_id == "admin_off_limits"


def test_with_duplicate_form_values_the_app_picks_one_so_the_gate_refuses_to_guess(world):
    client, gate = world
    body = "vendor_id=5&invoice_no=INV-3107&amount=100&amount=80000&due_date=10%2F11%2F2026"
    client.post("/erp/bills/new", content=body, headers={"content-type": "application/x-www-form-urlencoded"})
    stored = query(client, "SELECT amount FROM erp_bills WHERE invoice_no = 'INV-3107'")[0]["amount"]
    assert stored == 80000.0  # observed here: the LAST value wins. A gate reading the first would see 100.
    request = GateRequest("POST", BASE + "/erp/bills/new", body, "application/x-www-form-urlencoded")
    assert gate.evaluate(request).rule_id == "unreadable_request"


# ---- the whole path ------------------------------------------------------

def test_an_over_limit_bill_is_blocked_then_created_after_approval_and_only_once(world):
    client, gate = world
    decision, response = send(client, gate, "POST", "/erp/bills/new", data=ZENITH)
    assert decision.effect == "require_approval" and response is None
    assert len(query(client, "SELECT * FROM erp_bills")) == 2  # nothing was written

    gate.approvals.decide(decision.key, True)
    decision, response = send(client, gate, "POST", "/erp/bills/new", data=ZENITH)
    assert decision.allowed and response.status_code == 303
    assert len(query(client, "SELECT * FROM erp_bills")) == 3

    decision, response = send(client, gate, "POST", "/erp/bills/new", data=ZENITH)  # a second copy
    assert decision.effect == "require_approval" and response is None
    assert len(query(client, "SELECT * FROM erp_bills")) == 3


def test_a_denied_approval_means_no_write(world):
    client, gate = world
    decision, _ = send(client, gate, "POST", "/erp/bills/new", data=ZENITH)
    gate.approvals.decide(decision.key, False)
    decision, response = send(client, gate, "POST", "/erp/bills/new", data=ZENITH)
    assert decision.effect == "deny" and response is None
    assert len(query(client, "SELECT * FROM erp_bills")) == 2


def test_mark_paid_uses_the_real_amount_from_the_system(world):
    client, gate = world
    # make a big bill exist (as an admin-approved earlier action), then try to mark it paid
    client.post("/erp/api/bills", json={"vendor_id": 5, "invoice_no": "BIG", "amount": 80000, "due_date": "2026-11-10"})
    decision, response = send(client, gate, "POST", "/erp/bills/3/mark_paid")
    assert (decision.effect, decision.rule_id) == ("require_approval", "paid_over_limit") and response is None
    assert query(client, "SELECT status FROM erp_bills WHERE id = 3")[0]["status"] == "Open"

    decision, response = send(client, gate, "POST", "/erp/bills/2/mark_paid")  # the 36,450 bill
    assert decision.allowed and response.status_code == 303
    assert query(client, "SELECT status FROM erp_bills WHERE id = 2")[0]["status"] == "Paid"


def test_the_ui_edit_that_raises_an_amount_past_the_limit_is_blocked(world):
    client, gate = world
    decision, response = send(client, gate, "POST", "/erp/bills/1/edit", data={"amount": "90000", "due_date": "05/08/2026"})
    assert decision.rule_id == "update_over_limit" and response is None
    assert query(client, "SELECT amount FROM erp_bills WHERE id = 1")[0]["amount"] == 24500.0
