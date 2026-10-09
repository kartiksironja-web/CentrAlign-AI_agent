"""Each fault flag does exactly what faults.py says, and nothing when off."""
import pytest

from apps.company.faults import FaultConflict, FaultState, parse_fault_list, sync_fault_rows
from tests.helpers import buttons, query

NEW_BILL = {"vendor_id": "7", "invoice_no": "INV-2041", "amount": "48200", "due_date": "03/11/2026"}


def test_fault_state_validates_names():
    assert FaultState(["DATE_TRAP"]).has("DATE_TRAP")
    assert FaultState().names == []
    with pytest.raises(ValueError, match="unknown fault"):
        FaultState(["NOPE"])


def test_parse_fault_list():
    assert parse_fault_list("DATE_TRAP, SAVE_BROKEN") == ["DATE_TRAP", "SAVE_BROKEN"]
    assert parse_fault_list("") == [] and parse_fault_list(None) == []


def test_all_faults_are_off_by_default(client):
    assert client.app.state.faults.names == []


# ---- DATE_TRAP -----------------------------------------------------------

def test_without_trap_03_11_is_3_november(client):
    client.post("/erp/bills/new", data=NEW_BILL)
    assert query(client, "SELECT due_date FROM erp_bills WHERE invoice_no = 'INV-2041'")[0]["due_date"] == "2026-11-03"


def test_date_trap_hides_hint_and_saves_the_wrong_date_with_a_green_banner(make_client):
    client = make_client(faults=["DATE_TRAP"])
    assert "DD/MM/YYYY" not in client.get("/erp/bills/new").text

    r = client.post("/erp/bills/new", data=NEW_BILL)
    assert r.status_code == 303 and r.headers["location"] == "/erp/bills?flash=bill_saved"
    page = client.get(r.headers["location"]).text
    assert "Bill saved" in page and 'class="banner success"' in page  # looks like success...
    # ...but 03/11 was read as MM/DD: 11 March, not 3 November.
    assert query(client, "SELECT due_date FROM erp_bills WHERE invoice_no = 'INV-2041'")[0]["due_date"] == "2026-03-11"
    assert "11/03/2026" in page  # the wrong value is visible on the list, so a careful re-read can catch it


def test_date_trap_is_only_silent_when_the_day_is_12_or_less(make_client):
    client = make_client(faults=["DATE_TRAP"])
    r = client.post("/erp/bills/new", data={**NEW_BILL, "due_date": "25/11/2026"})  # month 25 is invalid
    assert r.status_code == 422 and "Invalid due date" in r.text
    assert len(query(client, "SELECT * FROM erp_bills")) == 2


def test_date_trap_also_misreads_the_edit_form(make_client):
    client = make_client(faults=["DATE_TRAP"])
    client.post("/erp/bills/1/edit", data={"amount": "24500", "due_date": "07/09/2026"})
    assert query(client, "SELECT due_date FROM erp_bills WHERE id = 1")[0]["due_date"] == "2026-07-09"


def test_date_trap_does_not_affect_the_api(make_client):
    client = make_client(faults=["DATE_TRAP"])
    r = client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"})
    assert r.json()["due_date"] == "2026-11-03"


# ---- SAVE_BROKEN / BUTTON_RENAME -----------------------------------------

def test_save_broken_button_cannot_submit(make_client):
    html = make_client(faults=["SAVE_BROKEN"]).get("/erp/bills/new").text
    [button] = buttons(html)
    assert 'type="button"' in button and 'type="submit"' not in button
    assert 'onsubmit="return false;"' in html  # Enter key cannot submit either


def test_save_broken_leaves_the_api_working(make_client):
    client = make_client(faults=["SAVE_BROKEN"])
    assert client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "X", "amount": 5, "due_date": "2026-11-03"}).status_code == 201


def test_button_rename(make_client):
    [button] = buttons(make_client(faults=["BUTTON_RENAME"]).get("/erp/bills/new").text)
    assert 'id="submit-entry"' in button and "Submit Entry" in button
    assert "Save Bill" not in button and "save-bill" not in button


def test_both_button_faults_together(make_client):
    [button] = buttons(make_client(faults=["SAVE_BROKEN", "BUTTON_RENAME"]).get("/erp/bills/new").text)
    assert 'id="submit-entry"' in button and 'type="button"' in button


def test_button_faults_leave_other_forms_alone(make_client):
    client = make_client(faults=["SAVE_BROKEN", "BUTTON_RENAME"])
    assert 'id="save-vendor"' in client.get("/erp/vendors/new").text
    assert 'id="save-changes"' in client.get("/erp/bills/1/edit").text


# ---- DUPLICATE_VENDOR ----------------------------------------------------

def test_duplicate_vendor_appears_everywhere_and_changes_latest_invoice(make_client):
    client = make_client(faults=["DUPLICATE_VENDOR"])
    assert "Acme Supplies LLC" in client.get("/portal/vendors").text
    assert "Acme Supplies LLC (Dubai)" in client.get("/erp/bills/new").text
    assert "Acme Supplies LLC" in [v["name"] for v in client.get("/erp/api/vendors").json()]
    assert "INV-9001" in client.get("/portal/invoices").text
    newest_overall_acme = query(
        client,
        "SELECT i.invoice_no FROM portal_invoices i JOIN portal_vendors v ON v.id = i.vendor_id"
        " WHERE v.name LIKE 'Acme%' ORDER BY i.invoice_date DESC LIMIT 1",
    )[0]["invoice_no"]
    assert newest_overall_acme == "INV-9001"  # picking the wrong Acme gives a different 'latest'


def test_duplicate_vendor_is_removed_when_switched_off(make_client):
    client = make_client(faults=["DUPLICATE_VENDOR"])
    client.app.state.faults.set([])
    from apps.company.db import connect

    conn = connect(client.app.state.db_path)
    sync_fault_rows(conn, client.app.state.faults)
    conn.close()
    assert "Acme Supplies LLC" not in client.get("/portal/vendors").text
    assert query(client, "SELECT * FROM portal_invoices WHERE invoice_no = 'INV-9001'") == []


def test_duplicate_vendor_conflict_is_loud_not_silent(client):
    client.post("/erp/api/vendors", json={"name": "Globex", "payment_terms_days": 30})  # takes id 8
    from apps.company.db import connect

    faults = FaultState(["DUPLICATE_VENDOR"])
    conn = connect(client.app.state.db_path)
    with pytest.raises(FaultConflict):
        sync_fault_rows(conn, faults)
    conn.close()
