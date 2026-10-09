"""/admin: token-protected reset and fault switching; no route map leaks."""
from tests.conftest import ADMIN
from tests.helpers import query


def test_admin_needs_the_token(client):
    for method, url in (("get", "/admin/faults"), ("post", "/admin/reset")):
        assert getattr(client, method)(url).status_code == 403
        assert getattr(client, method)(url, headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_admin_is_disabled_when_no_token_is_configured(make_client):
    client = make_client(admin_token=None)
    assert client.get("/admin/faults", headers=ADMIN).status_code == 503
    assert client.post("/admin/reset", headers=ADMIN).status_code == 503


def test_reset_restores_the_seeded_state(client):
    client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "X", "amount": 5, "due_date": "2026-11-03"})
    client.put("/erp/api/bills/1", json={"due_date": "2026-09-07"})
    assert client.post("/admin/reset", headers=ADMIN).json()["status"] == "reset"
    bills = query(client, "SELECT id, invoice_no, due_date FROM erp_bills ORDER BY id")
    assert bills == [{"id": 1, "invoice_no": "INV-2033", "due_date": "2026-08-05"},
                     {"id": 2, "invoice_no": "INV-2039", "due_date": "2026-10-05"}]


def test_get_and_set_faults(client):
    assert client.get("/admin/faults", headers=ADMIN).json()["active"] == []
    r = client.post("/admin/faults", headers=ADMIN, json={"faults": ["DATE_TRAP", "BUTTON_RENAME"]})
    assert r.json()["active"] == ["BUTTON_RENAME", "DATE_TRAP"]
    assert "Submit Entry" in client.get("/erp/bills/new").text  # takes effect immediately
    client.post("/admin/faults", headers=ADMIN, json={"faults": []})
    assert "Save Bill" in client.get("/erp/bills/new").text


def test_unknown_fault_is_rejected_and_changes_nothing(client):
    client.post("/admin/faults", headers=ADMIN, json={"faults": ["DATE_TRAP"]})
    r = client.post("/admin/faults", headers=ADMIN, json={"faults": ["NOPE"]})
    assert r.status_code == 422 and "unknown fault" in r.text
    assert client.get("/admin/faults", headers=ADMIN).json()["active"] == ["DATE_TRAP"]


def test_switching_duplicate_vendor_on_and_off_via_admin(client):
    client.post("/admin/faults", headers=ADMIN, json={"faults": ["DUPLICATE_VENDOR"]})
    assert "Acme Supplies LLC" in client.get("/portal/vendors").text
    client.post("/admin/faults", headers=ADMIN, json={"faults": []})
    assert "Acme Supplies LLC" not in client.get("/portal/vendors").text


def test_duplicate_vendor_conflict_gives_409_and_keeps_old_faults(client):
    client.post("/erp/api/vendors", json={"name": "Globex", "payment_terms_days": 30})  # takes id 8
    r = client.post("/admin/faults", headers=ADMIN, json={"faults": ["DUPLICATE_VENDOR"]})
    assert r.status_code == 409
    assert client.get("/admin/faults", headers=ADMIN).json()["active"] == []


def test_reset_keeps_duplicate_rows_while_that_fault_is_on(make_client):
    client = make_client(faults=["DUPLICATE_VENDOR"])
    client.post("/admin/reset", headers=ADMIN)
    assert "Acme Supplies LLC" in client.get("/portal/vendors").text


def test_no_auto_docs_or_openapi_route_map(client):
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code == 404
