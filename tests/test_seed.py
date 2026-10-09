"""The seeded world is exactly what the docs and evals say it is."""
from datetime import date

import pdfplumber

from apps.company.seed import VENDOR_FORM_NAME, seed_database
from apps.company.world import INVOICES
from tests.helpers import query, snapshot


def test_row_counts(client):
    counts = {t: len(rows) for t, rows in snapshot(client).items()}
    assert counts == {"portal_vendors": 3, "portal_invoices": 9, "erp_vendors": 3, "erp_bills": 2}


def test_newest_acme_invoice_is_inv_2041(client):
    newest = query(
        client, "SELECT * FROM portal_invoices WHERE vendor_id = 7 ORDER BY invoice_date DESC LIMIT 1"
    )[0]
    assert (newest["invoice_no"], newest["amount"], newest["due_date"]) == ("INV-2041", 48200.0, "2026-11-03")


def test_newest_zenith_invoice_is_over_the_approval_limit(client):
    newest = query(
        client, "SELECT * FROM portal_invoices WHERE vendor_id = 5 ORDER BY invoice_date DESC LIMIT 1"
    )[0]
    assert (newest["invoice_no"], newest["amount"]) == ("INV-3107", 80000.0)


def test_revised_invoice_never_competes_for_latest(client):
    revised = query(client, "SELECT * FROM portal_invoices WHERE invoice_no = 'INV-2033-R1'")[0]
    older_bills = query(client, "SELECT invoice_date FROM portal_invoices WHERE invoice_no IN ('INV-2038', 'INV-2039', 'INV-2041')")
    assert revised["revised_of"] == "INV-2033"
    assert all(revised["invoice_date"] < r["invoice_date"] for r in older_bills)


def test_date_trap_would_be_silent_for_the_tasks_dates(client):
    """Day <= 12 means reading DD/MM as MM/DD still gives a VALID (wrong) date."""
    for invoice_no in ("INV-2041", "INV-3107", "INV-2033-R1"):
        due = query(client, "SELECT due_date FROM portal_invoices WHERE invoice_no = ?", (invoice_no,))[0]["due_date"]
        assert date.fromisoformat(due).day <= 12, invoice_no


def test_vendor_ids_and_names_match_between_portal_and_erp(client):
    portal = query(client, "SELECT id, name FROM portal_vendors ORDER BY id")
    erp = query(client, "SELECT id, name FROM erp_vendors ORDER BY id")
    assert portal == erp


def test_seed_is_deterministic(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    seed_database(first)
    seed_database(second)

    class Fake:  # query() only needs .app.state.db_path
        def __init__(self, d):
            self.app = type("A", (), {"state": type("S", (), {"db_path": d / "company.db"})()})()

    assert snapshot(Fake(first)) == snapshot(Fake(second))


def test_reseed_over_existing_database_resets_it(client):
    db_dir = client.app.state.data_dir
    client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "X", "amount": 1, "due_date": "2026-11-03"})
    assert len(query(client, "SELECT * FROM erp_bills")) == 3
    seed_database(db_dir)
    assert len(query(client, "SELECT * FROM erp_bills")) == 2


def test_every_invoice_pdf_matches_its_database_row(client):
    """The PDF is where the agent reads amount and due date, so it must agree with the DB."""
    data_dir = client.app.state.data_dir
    for inv in INVOICES:
        with pdfplumber.open(data_dir / inv.pdf_path) as pdf:
            text = "\n".join(page.extract_text() for page in pdf.pages)
        d, m, y = inv.due_date[8:], inv.due_date[5:7], inv.due_date[:4]
        assert f"Invoice No: {inv.invoice_no}" in text
        assert f"Amount Due: INR {inv.amount:,.2f}" in text, inv.invoice_no
        assert f"Due Date: {d}/{m}/{y}" in text, inv.invoice_no


def test_revised_pdf_says_it_replaces_the_original(client):
    with pdfplumber.open(client.app.state.data_dir / "portal_files" / "INV-2033-R1.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    assert "REVISED INVOICE" in text and "Revision of: INV-2033" in text


def test_vendor_form_pdf_is_written_to_inputs(tmp_path):
    seed_database(tmp_path / "data", inputs_dir=tmp_path / "inputs")
    with pdfplumber.open(tmp_path / "inputs" / VENDOR_FORM_NAME) as pdf:
        text = pdf.pages[0].extract_text()
    assert "Company Name: Globex Components Pvt Ltd" in text
    assert "Payment Terms (days): 45" in text
