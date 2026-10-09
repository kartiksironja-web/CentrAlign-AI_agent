"""The ERP web UI with no faults: forms, banners, validation."""
from tests.helpers import buttons, query, snapshot

NEW_BILL = {"vendor_id": "7", "invoice_no": "INV-2041", "amount": "48200", "due_date": "03/11/2026"}


def test_bill_list_shows_dates_day_first_from_iso_storage(client):
    html = client.get("/erp/bills").text
    assert "05/08/2026" in html  # INV-2033, stored as 2026-08-05
    assert "2026-08-05" not in html


def test_new_bill_form_has_format_hint_and_save_button(client):
    html = client.get("/erp/bills/new").text
    assert "DD/MM/YYYY" in html
    [button] = buttons(html)
    assert 'id="save-bill"' in button and 'type="submit"' in button and "Save Bill" in button


def test_create_bill_via_ui(client):
    r = client.post("/erp/bills/new", data=NEW_BILL)
    assert r.status_code == 303 and r.headers["location"] == "/erp/bills?flash=bill_saved"
    assert "Bill saved" in client.get(r.headers["location"]).text
    [row] = query(client, "SELECT * FROM erp_bills WHERE invoice_no = 'INV-2041'")
    assert (row["vendor_id"], row["amount"], row["due_date"], row["status"], row["created_via"]) == (
        7, 48200.0, "2026-11-03", "Open", "ui",
    )


def test_bad_due_date_is_rejected_visibly_and_writes_nothing(client):
    before = snapshot(client)
    r = client.post("/erp/bills/new", data={**NEW_BILL, "due_date": "2026-11-03"})
    assert r.status_code == 422 and "Due date must be DD/MM/YYYY" in r.text
    assert snapshot(client) == before


def test_missing_vendor_bad_amount_missing_invoice_are_rejected(client):
    before = snapshot(client)
    for bad in ({"vendor_id": ""}, {"amount": "-5"}, {"amount": "abc"}, {"invoice_no": "  "}, {"vendor_id": "99"}):
        r = client.post("/erp/bills/new", data={**NEW_BILL, **bad})
        assert r.status_code == 422, bad
        assert 'role="alert"' in r.text, bad
    assert snapshot(client) == before


def test_form_keeps_what_was_typed_after_an_error(client):
    html = client.post("/erp/bills/new", data={**NEW_BILL, "amount": "-5"}).text
    assert 'value="INV-2041"' in html and 'value="03/11/2026"' in html


def test_create_vendor_via_ui(client):
    r = client.post("/erp/vendors/new", data={
        "name": "Globex Components Pvt Ltd", "city": "Bengaluru", "gst_no": "29AABCG1234F1Z7",
        "email": "accounts@globex-components.example", "payment_terms_days": "45",
    })
    assert r.status_code == 303 and r.headers["location"] == "/erp/vendors?flash=vendor_saved"
    [row] = query(client, "SELECT * FROM erp_vendors WHERE name LIKE 'Globex%'")
    assert row["payment_terms_days"] == 45 and row["city"] == "Bengaluru"


def test_vendor_needs_a_name_and_whole_number_terms(client):
    assert client.post("/erp/vendors/new", data={"name": "", "payment_terms_days": "30"}).status_code == 422
    assert client.post("/erp/vendors/new", data={"name": "X", "payment_terms_days": "soon"}).status_code == 422
    assert len(query(client, "SELECT * FROM erp_vendors")) == 3


def test_edit_bill_changes_only_that_bills_due_date(client):
    before = snapshot(client)
    r = client.post("/erp/bills/1/edit", data={"amount": "24500.00", "due_date": "07/09/2026"})
    assert r.status_code == 303 and r.headers["location"] == "/erp/bills?flash=bill_updated"
    after = snapshot(client)
    assert after["erp_bills"][0]["due_date"] == "2026-09-07"
    assert after["erp_bills"][0] | {"due_date": "2026-08-05"} == before["erp_bills"][0]
    assert after["erp_bills"][1:] == before["erp_bills"][1:]
    assert {t: r for t, r in after.items() if t != "erp_bills"} == {t: r for t, r in before.items() if t != "erp_bills"}


def test_edit_form_is_prefilled_day_first(client):
    html = client.get("/erp/bills/1/edit").text
    assert 'value="05/08/2026"' in html and 'value="24500.00"' in html


def test_edit_unknown_bill_is_404(client):
    assert client.get("/erp/bills/99/edit").status_code == 404
    assert client.post("/erp/bills/99/edit", data={"amount": "1", "due_date": "01/01/2026"}).status_code == 404


def test_mark_paid_then_again(client):
    r = client.post("/erp/bills/2/mark_paid")
    assert r.status_code == 303 and r.headers["location"] == "/erp/bills?flash=bill_paid"
    assert query(client, "SELECT status FROM erp_bills WHERE id = 2")[0]["status"] == "Paid"
    again = client.post("/erp/bills/2/mark_paid")
    assert again.status_code == 409 and "only Open bills" in again.text


def test_paid_bill_has_no_mark_paid_button(client):
    client.post("/erp/bills/2/mark_paid")
    html = client.get("/erp/bills").text
    assert 'id="mark-paid-1"' in html and 'id="mark-paid-2"' not in html


def test_pages_never_link_to_admin(client):
    for url in ("/", "/erp/bills", "/erp/bills/new", "/erp/vendors", "/portal/vendors", "/portal/invoices"):
        assert "/admin" not in client.get(url).text, url
