"""The gate's real lookup path: default_fetch over actual HTTP to a running server.

Everything else in the gate tests injects a stand-in for the lookup. This test starts the
mock company with uvicorn on a free port, in a background thread, and lets the gate use
its own default_fetch (httpx) to read a bill's amount.
"""
import socket
import threading
import time

import pytest
import uvicorn

from agent.connectors import load_connectors
from agent.gate import Gate, GateRequest, default_fetch
from agent.policy import load_policy
from apps.company.app import create_app
from apps.company.seed import seed_database
from tests.agent.helpers import PACK


@pytest.fixture
def live_url(tmp_path):
    seed_database(tmp_path / "data")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "data"), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "test server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_default_fetch_reads_a_real_response(live_url):
    status, text = default_fetch(live_url + "/erp/api/bills/2")
    assert status == 200 and '"amount":36450.0' in text
    assert default_fetch(live_url + "/erp/api/bills/999")[0] == 404


def test_the_gate_looks_up_over_real_http_with_its_default_fetch(live_url):
    gate = Gate(load_policy(PACK / "policy.yaml"),
                load_connectors(PACK / "connectors", {"erp": live_url, "portal": live_url}))
    ok = gate.evaluate(GateRequest("POST", live_url + "/erp/bills/2/mark_paid"))
    assert ok.allowed and ok.fields == {"bill_id": "2", "amount": 36450.0}
    missing = gate.evaluate(GateRequest("POST", live_url + "/erp/bills/999/mark_paid"))
    assert (missing.effect, missing.rule_id) == ("deny", "lookup_failed")


def test_an_unreachable_system_denies(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"  # nothing listens here once the socket closes
    gate = Gate(load_policy(PACK / "policy.yaml"),
                load_connectors(PACK / "connectors", {"erp": dead, "portal": dead}))
    d = gate.evaluate(GateRequest("POST", dead + "/erp/bills/2/mark_paid"))
    assert (d.effect, d.rule_id) == ("deny", "lookup_failed")
