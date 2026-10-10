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


SEED_VENDORS = [
    {"id": 3, "name": "Kaveri Packaging Co"},
    {"id": 5, "name": "Zenith Industrial Ltd"},
    {"id": 7, "name": "Acme Supplies Pvt Ltd"},
]
LOOKALIKE = {"id": 8, "name": "Acme Supplies LLC"}  # what the DUPLICATE_VENDOR fault adds


def vendors_fetch(vendors=SEED_VENDORS, calls: list | None = None):
    """A stand-in for GET /erp/api/vendors. Any other URL is a mistake in the test, so it raises."""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if not url.endswith("/erp/api/vendors"):
            raise AssertionError(f"the gate should only have listed the vendors, but asked for {url}")
        return 200, json.dumps(vendors)
    return fetch


def world_fetch(amounts: dict[str, object] | None = None, vendors=SEED_VENDORS):
    """Both stand-ins in one: the vendor list, and single bills by id."""
    bills, listing = bills_fetch(amounts or {}), vendors_fetch(vendors)
    return lambda url: listing(url) if url.endswith("/erp/api/vendors") else bills(url)


def make_gate(fetch=None, base_urls=None):
    """The real pack. By default the gate may list the vendors (for the look-alike check) and nothing else."""
    return Gate(load_policy(PACK / "policy.yaml"), load_connectors(PACK / "connectors", base_urls),
                fetch=fetch or vendors_fetch())


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
