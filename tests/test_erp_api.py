"""The ERP JSON API: same rules as the UI, strict ISO dates, server-set created_via."""
from tests.helpers import query, snapshot

NEW_BILL = {"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"}


def test_create_bill(client):
    r = client.post("/erp/api/bills", json=NEW_BILL)
    assert r.status_code == 201
    body = r.json()
    assert body | {"id": 0} == {**NEW_BILL, "amount": 48200.0, "status": "Open", "created_via": "api", "id": 0}
    assert query(client, "SELECT created_via FROM erp_bills WHERE id = ?", (body["id"],)) == [{"created_via": "api"}]


def test_client_cannot_choose_created_via(client):
    r = client.post("/erp/api/bills", json={**NEW_BILL, "created_via": "ui"})
    assert r.status_code == 201 and r.json()["created_via"] == "api"


def test_day_first_date_is_rejected_with_a_helpful_message(client):
    before = snapshot(client)
    r = client.post("/erp/api/bills", json={**NEW_BILL, "due_date": "03/11/2026"})
    assert r.status_code == 422 and "YYYY-MM-DD" in r.text
    assert snapshot(client) == before


def test_unknown_vendor_and_bad_amount_are_rejected(client):
    assert client.post("/erp/api/bills", json={**NEW_BILL, "vendor_id": 99}).status_code == 422
    assert client.post("/erp/api/bills", json={**NEW_BILL, "amount": 0}).status_code == 422
    assert client.post("/erp/api/bills", json={**NEW_BILL, "invoice_no": " "}).status_code == 422
    assert len(query(client, "SELECT * FROM erp_bills")) == 2


def test_list_and_filter_and_get_bills(client):
    assert [b["invoice_no"] for b in client.get("/erp/api/bills").json()] == ["INV-2033", "INV-2039"]
    assert [b["id"] for b in client.get("/erp/api/bills?invoice_no=INV-2039").json()] == [2]
    assert client.get("/erp/api/bills?vendor_id=5").json() == []
    assert client.get("/erp/api/bills/1").json()["invoice_no"] == "INV-2033"
    assert client.get("/erp/api/bills/99").status_code == 404


def test_put_changes_due_date_only(client):
    before = client.get("/erp/api/bills/1").json()
    r = client.put("/erp/api/bills/1", json={"due_date": "2026-09-07"})
    assert r.status_code == 200
    assert r.json() == before | {"due_date": "2026-09-07"}


def test_put_status_paid_then_again_is_409_and_reopen_is_refused(client):
    assert client.put("/erp/api/bills/2", json={"status": "Paid"}).json()["status"] == "Paid"
    assert client.put("/erp/api/bills/2", json={"status": "Paid"}).status_code == 409
    assert client.put("/erp/api/bills/2", json={"status": "Open"}).status_code == 422


def test_put_failed_validation_changes_nothing(client):
    before = snapshot(client)
    assert client.put("/erp/api/bills/1", json={"amount": -1, "due_date": "2026-09-07"}).status_code == 422
    assert client.put("/erp/api/bills/1", json={"due_date": "07/09/2026"}).status_code == 422
    assert client.put("/erp/api/bills/99", json={"amount": 5}).status_code == 404
    assert snapshot(client) == before


def test_vendors_list_and_create(client):
    assert [v["name"] for v in client.get("/erp/api/vendors").json()] == [
        "Kaveri Packaging Co", "Zenith Industrial Ltd", "Acme Supplies Pvt Ltd",
    ]
    r = client.post("/erp/api/vendors", json={"name": "Globex Components Pvt Ltd", "payment_terms_days": 45})
    assert r.status_code == 201 and r.json()["payment_terms_days"] == 45
    assert client.post("/erp/api/vendors", json={"name": "", "payment_terms_days": 30}).status_code == 422
    assert client.post("/erp/api/vendors", json={"name": "No terms"}).status_code == 422


def test_api_has_no_delete(client):
    assert client.delete("/erp/api/bills/1").status_code == 405
    assert len(query(client, "SELECT * FROM erp_bills")) == 2
