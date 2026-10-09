"""Builders shared by the gate tests."""
import json
from pathlib import Path
from urllib.parse import urlencode

from agent.connectors import load_connectors
from agent.gate import Gate, GateRequest
from agent.policy import load_policy

PACK = Path(__file__).resolve().parents[2] / "company_pack"
BASE = "http://127.0.0.1:8000"


def no_fetch(url):
    raise AssertionError(f"the gate should not have looked anything up, but asked for {url}")


def make_gate(fetch=no_fetch, base_urls=None):
    return Gate(load_policy(PACK / "policy.yaml"), load_connectors(PACK / "connectors", base_urls), fetch=fetch)


def bills_fetch(amounts: dict[str, object], calls: list | None = None):
    """A stand-in for the system's own API: GET /erp/api/bills/<id> -> {"amount": ...}."""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        bill_id = url.rsplit("/", 1)[1]
        if bill_id not in amounts:
            return 404, json.dumps({"detail": "not found"})
        return 200, json.dumps({"id": int(bill_id), "amount": amounts[bill_id]})
    return fetch


def get(path, base=BASE):
    return GateRequest("GET", base + path)


def form(path, base=BASE, method="POST", **fields):
    return GateRequest(method, base + path, urlencode(fields), "application/x-www-form-urlencoded")


def js(method, path, obj, base=BASE):
    return GateRequest(method, base + path, json.dumps(obj), "application/json")
