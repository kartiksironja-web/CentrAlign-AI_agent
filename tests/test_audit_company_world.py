"""The auditor against the real mock company, with NO LLM involved.

A scripted "agent" (plain HTTP calls) does the work, then the declared assertions are
audited. This is the deterministic proof that the auditor catches mistakes that look
like success to the agent: the DATE_TRAP "Bill saved" banner, a wrong invoice, a
duplicate, a quiet side effect.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.company.app import create_app
from apps.company.seed import seed_database
from auditor.assertions import parse_assertions_yaml
from auditor.config import load_config
from auditor.engine import run_audit
from auditor.feedback import feedback
from auditor.readonly import ReadOnlyDbs
from auditor.snapshot import take_snapshot
from auditor.validate import Requirements

PACK = Path(__file__).resolve().parents[1] / "company_pack" / "audit.yaml"


@pytest.fixture
def company(tmp_path):
    """build(faults) -> (client, audit) where audit(yaml, requirements) judges the CURRENT state."""
    opened = []

    def build(faults=()):
        data_dir, inputs = tmp_path / "data", tmp_path / "inputs"
        seed_database(data_dir, inputs_dir=inputs)
        client = TestClient(create_app(data_dir, faults=faults, admin_token="t"), follow_redirects=False)
        dbs = ReadOnlyDbs(load_config(PACK, {"data": data_dir, "repo": tmp_path}))
        opened.append(dbs)
        before = take_snapshot(dbs)

        def audit(yaml_text, requirements=Requirements()):
            return run_audit(dbs, parse_assertions_yaml(yaml_text), requirements, before)

        return client, audit

    yield build
    for dbs in opened:
        dbs.close()


def latest_acme_check(field, invoice_no="INV-2041"):
    return f"""
  - id: {field}
    kind: field_equals
    actual: {{db: erp, table: erp_bills, where: {{invoice_no: {invoice_no}}}, field: {field}}}
    expected: {{from: query, db: portal, table: portal_invoices, where: {{vendor_id: 7}}, order_by: "invoice_date desc", limit: 1, field: {field}}}
"""


def t1_assertions(invoice_no="INV-2041", rows=1):
    checks = "".join(latest_acme_check(f, invoice_no) for f in ("vendor_id", "invoice_no", "amount", "due_date"))
    return ("assertions:" + checks
            + f"  - {{kind: rows_added, table: erp_bills, count: {rows}}}\n"
            + "  - {kind: no_other_changes, except_tables: [erp_bills]}\n")


T1_REQUIRES = Requirements(done_requires={"erp_bills": ["vendor_id", "invoice_no", "amount", "due_date"]})
NEW_BILL = {"vendor_id": "7", "invoice_no": "INV-2041", "amount": "48200", "due_date": "03/11/2026"}


def failed_ids(result):
    return [c.id for c in result.failed]


# ---- T1: create a bill from the latest invoice ---------------------------

def test_t1_done_correctly_through_the_ui_passes(company):
    client, audit = company()
    client.post("/erp/bills/new", data=NEW_BILL)
    result = audit(t1_assertions(), T1_REQUIRES)
    assert result.passed, result.as_dict()


def test_t1_done_correctly_through_the_api_passes(company):
    client, audit = company()
    client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"})
    assert audit(t1_assertions(), T1_REQUIRES).passed


def test_t1_date_trap_the_silent_wrong_date_is_caught(company):
    """The ERP shows a green 'Bill saved' banner, but 03/11 was stored as 11 March."""
    client, audit = company(faults=["DATE_TRAP"])
    r = client.post("/erp/bills/new", data=NEW_BILL)
    assert "Bill saved" in client.get(r.headers["location"]).text  # what the agent would see
    result = audit(t1_assertions(), T1_REQUIRES)
    assert not result.passed
    assert failed_ids(result) == ["due_date"]  # exactly the damaged field; everything else is right
    c = result.failed[0]
    assert (c.expected, c.actual) == ("2026-11-03", "2026-03-11")
    round_1 = feedback(result, 1)
    assert "due_date" in round_1 and "2026-03-11" not in round_1 and "2026-11-03" not in round_1


def test_t1_repair_after_a_failed_audit_passes(company):
    client, audit = company(faults=["DATE_TRAP"])
    client.post("/erp/bills/new", data=NEW_BILL)
    assert not audit(t1_assertions(), T1_REQUIRES).passed
    client.put("/erp/api/bills/3", json={"due_date": "2026-11-03"})  # the API is not affected by the trap
    assert audit(t1_assertions(), T1_REQUIRES).passed


def test_t1_wrong_invoice_is_caught(company):
    client, audit = company()
    client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "INV-2038", "amount": 29990, "due_date": "2026-09-03"})
    result = audit(t1_assertions("INV-2038"), T1_REQUIRES)
    assert "invoice_no" in failed_ids(result) and not result.passed


def test_t1_wrong_vendor_is_caught_when_the_duplicate_vendor_exists(company):
    client, audit = company(faults=["DUPLICATE_VENDOR"])
    client.post("/erp/api/bills", json={"vendor_id": 8, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"})
    assert "vendor_id" in failed_ids(audit(t1_assertions(), T1_REQUIRES))


def test_t1_duplicate_bill_is_caught(company):
    client, audit = company()
    for _ in range(2):
        client.post("/erp/bills/new", data=NEW_BILL)
    result = audit(t1_assertions(rows=2), T1_REQUIRES)  # even if the agent "expects" two rows
    assert not result.passed and "2 rows match" in result.failed[0].detail


def test_t1_quiet_side_effect_is_caught(company):
    client, audit = company()
    client.post("/erp/bills/new", data=NEW_BILL)
    client.post("/erp/bills/2/mark_paid")  # nobody asked for this
    result = audit(t1_assertions(), T1_REQUIRES)
    assert failed_ids(result) == ["no_other_changes_6"]
    assert "erp.erp_bills: 1 row(s) changed" in result.failed[0].actual


def test_t1_nothing_done_is_caught(company):
    _, audit = company()
    assert not audit(t1_assertions(), T1_REQUIRES).passed


def test_t1_checking_the_amount_against_itself_is_refused(company):
    client, audit = company()
    client.post("/erp/bills/new", data=NEW_BILL)
    weak = t1_assertions().replace(
        latest_acme_check("amount"),
        """
  - id: amount
    kind: field_equals
    actual: {db: erp, table: erp_bills, where: {invoice_no: INV-2041}, field: amount}
    expected: {from: literal, value: 48200}
""",
    )
    result = audit(weak, T1_REQUIRES)
    assert not result.valid and "independent source" in result.validation_errors[0]


# ---- T2: update a bill from a revised invoice document -------------------

T2_REQUIRES = Requirements(done_requires={"erp_bills": ["invoice_no", "due_date"]})
T2 = """
assertions:
  - id: due_date
    kind: field_equals
    actual: {db: erp, table: erp_bills, where: {invoice_no: INV-2033}, field: due_date}
    expected: {from: file, path: portal_files/INV-2033-R1.pdf, pattern: "Due Date: (\\\\d{2}/\\\\d{2}/\\\\d{4})", as: date_dmy}
  - id: invoice_no
    kind: field_equals
    actual: {db: erp, table: erp_bills, where: {invoice_no: INV-2033}, field: invoice_no}
    expected: {from: file, path: portal_files/INV-2033-R1.pdf, pattern: "Revision of: (INV-\\\\d+)", as: text}
  - {kind: rows_changed, table: erp_bills, count: 1}
  - {kind: no_other_changes, except_tables: [erp_bills]}
"""


def test_t2_correct_update_from_the_revised_document_passes(company):
    client, audit = company()
    client.post("/erp/bills/1/edit", data={"amount": "24500", "due_date": "07/09/2026"})
    result = audit(T2, T2_REQUIRES)
    assert result.passed, result.as_dict()


def test_t2_with_date_trap_is_caught(company):
    client, audit = company(faults=["DATE_TRAP"])
    client.post("/erp/bills/1/edit", data={"amount": "24500", "due_date": "07/09/2026"})  # stored as 9 July
    result = audit(T2, T2_REQUIRES)
    assert failed_ids(result) == ["due_date"]


def test_t2_updating_the_wrong_bill_is_caught(company):
    client, audit = company()
    client.put("/erp/api/bills/2", json={"due_date": "2026-09-07"})  # INV-2039 instead of INV-2033
    result = audit(T2, T2_REQUIRES)
    assert "due_date" in failed_ids(result)


def test_t2_adding_a_new_bill_instead_of_updating_is_caught(company):
    client, audit = company()
    client.post("/erp/api/bills", json={"vendor_id": 7, "invoice_no": "INV-2033", "amount": 24500, "due_date": "2026-09-07"})
    result = audit(T2, T2_REQUIRES)
    assert not result.passed  # duplicate INV-2033 rows, one row added not changed


# ---- T3: create a vendor from a form file --------------------------------

T3_REQUIRES = Requirements(done_requires={"erp_vendors": ["name", "payment_terms_days"]})


def t3(name="Globex Components Pvt Ltd"):
    return f"""
assertions:
  - id: name
    kind: field_equals
    actual: {{db: erp, table: erp_vendors, where: {{name: "{name}"}}, field: name}}
    expected: {{from: file, path: inputs/new_vendor_form_globex.pdf, pattern: "Company Name: (.+)"}}
  - id: payment_terms_days
    kind: field_equals
    actual: {{db: erp, table: erp_vendors, where: {{name: "{name}"}}, field: payment_terms_days}}
    expected: {{from: file, path: inputs/new_vendor_form_globex.pdf, pattern: "Payment Terms \\\\(days\\\\): (\\\\d+)", as: number}}
  - {{kind: rows_added, table: erp_vendors, count: 1}}
  - {{kind: no_other_changes, except_tables: [erp_vendors]}}
"""


def test_t3_vendor_matching_the_form_passes(company):
    client, audit = company()
    client.post("/erp/api/vendors", json={"name": "Globex Components Pvt Ltd", "city": "Bengaluru", "payment_terms_days": 45})
    result = audit(t3(), T3_REQUIRES)
    assert result.passed, result.as_dict()


def test_t3_wrong_payment_terms_are_caught(company):
    client, audit = company()
    client.post("/erp/api/vendors", json={"name": "Globex Components Pvt Ltd", "payment_terms_days": 30})
    assert failed_ids(audit(t3(), T3_REQUIRES)) == ["payment_terms_days"]


# ---- no-write outcomes ---------------------------------------------------

def test_a_correct_refusal_is_confirmed_as_zero_rows_added(company):
    _, audit = company()
    result = audit("assertions:\n  - {kind: rows_added, table: erp_bills, count: 0}\n  - {kind: no_other_changes}")
    assert result.passed


def test_a_refusal_that_secretly_wrote_a_row_fails(company):
    client, audit = company()
    client.post("/erp/bills/new", data=NEW_BILL)
    result = audit("assertions:\n  - {kind: rows_added, table: erp_bills, count: 0}\n  - {kind: no_other_changes}")
    assert not result.passed
