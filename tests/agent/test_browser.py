"""The browser tool: a real Chromium operating the real mock company through the toolbox."""
import re

import pytest

from agent.detectors import Detectors
from agent.human import ScriptedHuman


def ref(text: str, kind: str, label: str) -> int:
    """The number of an element in a snapshot, found the way the model would: by what it says."""
    match = re.search(rf'\[(\d+)\] {re.escape(kind)} "{re.escape(label)}', text)
    assert match, f"no {kind} {label!r} in:\n{text}"
    return int(match.group(1))


@pytest.fixture
def session(company, browser_for):
    """session(faults=[...], human=None) -> (company, toolbox)"""
    def build(faults=(), human=None):
        comp = company(faults)
        return comp, comp.toolbox(human=human, browser=browser_for(comp))
    return build


def fill_bill(tb, vendor_label, invoice_no, amount, due, text=None):
    """Fill the new-bill form the way a model would, re-reading refs from each observation."""
    text = text or tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    text = tb.call("browser_select", {"ref": ref(text, "select", "Vendor"), "value": vendor_label}).text
    text = tb.call("browser_type", {"ref": ref(text, "input[text]", "Invoice number"), "text": invoice_no}).text
    text = tb.call("browser_type", {"ref": ref(text, "input[number]", "Amount"), "text": amount}).text
    return tb.call("browser_type", {"ref": ref(text, "input[text]", "Due date"), "text": due}).text


# ---- snapshots ---------------------------------------------------------------

def test_goto_returns_a_snapshot_with_text_tables_and_numbered_controls(session):
    comp, tb = session()
    result = tb.call("browser_goto", {"url": "/erp/bills"})
    assert not result.error and result.page_changed is True and not result.blocked
    text = result.text
    assert text.startswith(f"URL: {comp.url}/erp/bills")
    assert "Title: Bills - Nimbus" in text and "[table 1: 3 rows]" in text
    assert "INV-2033 | ₹24,500.00 | 05/08/2026 | Open" in text
    assert 'link "Edit"' in text and 'button "Mark paid"' in text and "href=/erp/bills/1/edit" in text
    assert result.page.title == "Bills - Nimbus" and result.page.status == 200


def test_the_new_bill_form_is_described_by_labels_types_and_current_values(session):
    _, tb = session()
    text = tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    assert re.search(r'\[\d+\] select "Vendor" value="-- select vendor --" options=\["-- select vendor --", "Kaveri Packaging Co \(Chennai\)", "Zenith', text)
    assert 'input[text] "Invoice number" value=""' in text
    assert 'input[number] "Amount (INR)" value=""' in text
    assert 'input[text] "Due date DD/MM/YYYY" value=""' in text
    assert 'button "Save Bill"' in text


def test_snapshot_tool_changes_nothing_and_is_not_a_page_action(session):
    _, tb = session()
    tb.call("browser_goto", {"url": "/erp/bills"})
    result = tb.call("browser_snapshot", {})
    assert result.page_changed is None and "Title: Bills - Nimbus" in result.text


# ---- doing work ------------------------------------------------------------------

def test_filling_and_saving_a_bill_with_refs_writes_it(session):
    comp, tb = session()
    text = fill_bill(tb, "Acme Supplies Pvt Ltd (Pune)", "INV-2041", "48200", "03/11/2026")
    assert 'value="03/11/2026"' in text and 'value="INV-2041"' in text  # typed values are visible in the snapshot
    result = tb.call("browser_click", {"ref": ref(text, "button", "Save Bill")})
    assert result.page_changed is True and "Bill saved" in result.text and "INV-2041 | ₹48,200.00 | 03/11/2026 | Open" in result.text
    [row] = comp.query("SELECT * FROM erp_bills WHERE invoice_no = 'INV-2041'")
    assert (row["vendor_id"], row["amount"], row["due_date"], row["created_via"]) == (7, 48200.0, "2026-11-03", "ui")


def test_a_select_accepts_the_visible_text_or_the_value(session):
    _, tb = session()
    text = tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    by_label = tb.call("browser_select", {"ref": ref(text, "select", "Vendor"), "value": "Zenith Industrial Ltd (Mumbai)"}).text
    by_value = tb.call("browser_select", {"ref": ref(text, "select", "Vendor"), "value": "7"}).text
    assert 'value="Zenith Industrial Ltd (Mumbai)"' in by_label and 'value="Acme Supplies Pvt Ltd (Pune)"' in by_value


def test_mark_paid_through_the_ui(session):
    comp, tb = session()
    text = tb.call("browser_goto", {"url": "/erp/bills"}).text
    # the second 'Mark paid' button belongs to the second bill (INV-2039)
    refs = [int(m) for m in re.findall(r'\[(\d+)\] button "Mark paid"', text)]
    result = tb.call("browser_click", {"ref": refs[1]})
    assert "Bill marked as paid" in result.text
    assert comp.query("SELECT status FROM erp_bills WHERE id = 2") == [{"status": "Paid"}]


# ---- the faults, as the agent experiences them ---------------------------------

def test_date_trap_looks_like_success_but_the_page_shows_the_damage(session):
    comp, tb = session(["DATE_TRAP"])
    form = tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    assert "DD/MM/YYYY" not in form  # the format hint is gone
    text = fill_bill(tb, "Acme Supplies Pvt Ltd (Pune)", "INV-2041", "48200", "03/11/2026", form)
    result = tb.call("browser_click", {"ref": ref(text, "button", "Save Bill")})
    assert "Bill saved" in result.text  # the green banner the agent will be tempted to trust...
    assert "INV-2041 | ₹48,200.00 | 11/03/2026 | Open" in result.text  # ...while the list shows 11 March
    assert comp.query("SELECT due_date FROM erp_bills WHERE invoice_no = 'INV-2041'") == [{"due_date": "2026-03-11"}]


def test_save_broken_click_changes_nothing_and_the_detector_notices(session):
    comp, tb = session(["SAVE_BROKEN"])
    text = fill_bill(tb, "Acme Supplies Pvt Ltd (Pune)", "INV-2041", "48200", "03/11/2026")
    result = tb.call("browser_click", {"ref": ref(text, "button", "Save Bill")})
    assert result.page_changed is False and not result.blocked and not result.error
    found = Detectors().inspect("browser_click", {"ref": 1}, result)
    assert [d.klass for d in found] == ["noop"]
    assert len(comp.query("SELECT * FROM erp_bills")) == 2


def test_button_rename_changes_what_the_model_sees(session):
    _, tb = session(["BUTTON_RENAME"])
    text = tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    assert 'button "Submit Entry"' in text and 'button "Save Bill"' not in text


def test_duplicate_vendor_shows_up_as_a_second_choice(session):
    _, tb = session(["DUPLICATE_VENDOR"])
    text = tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    assert "Acme Supplies Pvt Ltd (Pune)" in text and "Acme Supplies LLC (Dubai)" in text


# ---- the gate at the network layer --------------------------------------------------

def test_an_over_limit_save_is_blocked_in_the_browser_then_allowed_after_approval(session):
    comp, tb = session(human=ScriptedHuman({"approval": ["approve"]}))
    text = fill_bill(tb, "Zenith Industrial Ltd (Mumbai)", "INV-3107", "80000", "10/11/2026")
    save = ref(text, "button", "Save Bill")
    blocked = tb.call("browser_click", {"ref": save})
    assert blocked.text.startswith("BLOCKED: approval required (rule bill_over_limit). Use ask_human(kind=approval).")
    assert blocked.blocked[0].effect == "require_approval" and blocked.page_changed is False
    assert f"URL: {comp.url}/erp/bills/new" in blocked.text  # the browser stayed on the form...
    assert 'value="INV-3107"' in blocked.text and 'value="80000"' in blocked.text  # ...with everything still typed in
    assert Detectors().inspect("browser_click", {"ref": save}, blocked) == []  # a gate block is not a broken button
    assert len(comp.query("SELECT * FROM erp_bills")) == 2  # nothing was written

    approval = tb.call("ask_human", {"kind": "approval", "question": "Please approve this bill"})
    assert "APPROVED by the human: create_bill: vendor_id=5, invoice_no=INV-3107, amount=80000" in approval.text
    done = tb.call("browser_click", {"ref": save})
    assert "Bill saved" in done.text and not done.blocked
    assert comp.query("SELECT amount FROM erp_bills WHERE invoice_no = 'INV-3107'") == [{"amount": 80000.0}]


def test_navigating_to_admin_is_blocked_before_any_request_is_made(session):
    comp, tb = session()
    for url in ("/admin/faults", "/%61dmin/reset"):
        result = tb.call("browser_goto", {"url": url})
        assert not result.error and result.blocked[0].rule_id == "admin_off_limits"
        assert result.text.startswith("BLOCKED: denied by admin_off_limits")
    assert comp.query("SELECT COUNT(*) AS n FROM erp_bills") == [{"n": 2}]


def test_navigating_to_an_unknown_host_is_blocked(session):
    _, tb = session()
    result = tb.call("browser_goto", {"url": "http://example.com/"})
    assert result.blocked[0].rule_id == "host_policy" and "not in any connector manifest" in result.text


# ---- errors, downloads, error pages --------------------------------------------------

def test_a_stale_reference_is_a_clear_error(session):
    _, tb = session()
    tb.call("browser_goto", {"url": "/erp/bills"})
    result = tb.call("browser_click", {"ref": 999})
    assert result.error and "ref 999 is not on the current page" in result.text
    assert [d.klass for d in Detectors().inspect("browser_click", {"ref": 999}, result)] == ["tool_error"]


def test_a_reference_from_before_a_navigation_goes_stale(session):
    _, tb = session()
    text = tb.call("browser_goto", {"url": "/erp/bills/new"}).text
    save = ref(text, "button", "Save Bill")
    tb.call("browser_goto", {"url": "/erp/vendors"})
    result = tb.call("browser_click", {"ref": save + 50})
    assert result.error


def test_a_download_link_tells_the_agent_to_use_read_document(session):
    comp, tb = session()
    text = tb.call("browser_goto", {"url": "/portal/invoices/INV-2041"}).text
    assert 'link "Download PDF" href=/portal/files/INV-2041.pdf' in text
    clicked = tb.call("browser_click", {"ref": ref(text, "link", "Download PDF")})
    assert not clicked.error and clicked.page_changed is True
    assert f"started a file download from {comp.url}/portal/files/INV-2041.pdf" in clicked.text and "read_document" in clicked.text
    direct = tb.call("browser_goto", {"url": "/portal/files/INV-2041.pdf"})
    assert not direct.error and "started a file download" in direct.text


def test_an_error_page_is_visible_to_the_detector(session):
    _, tb = session()
    result = tb.call("browser_goto", {"url": "/portal/invoices/INV-0000"})
    assert result.page.status == 404 and "Title: Error - Nimbus" in result.text
    assert [d.klass for d in Detectors().inspect("browser_goto", {"url": "x"}, result)] == ["unexpected_page"]


def test_form_validation_feedback_is_normal_not_an_unexpected_page(session):
    _, tb = session()
    text = fill_bill(tb, "Acme Supplies Pvt Ltd (Pune)", "INV-2041", "48200", "2026-11-03")  # wrong date format
    result = tb.call("browser_click", {"ref": ref(text, "button", "Save Bill")})
    assert "Due date must be DD/MM/YYYY" in result.text and result.page.status == 422
    assert Detectors().inspect("browser_click", {"ref": 1}, result) == []
    assert 'value="2026-11-03"' in result.text  # what was typed is still in the form


def test_a_typed_password_is_never_echoed_back(session, browser_for):
    comp, tb = session()
    tb.browser.page.set_content('<form><label for="pw">Password</label><input id="pw" type="password"></form>')
    text = tb.call("browser_snapshot", {}).text
    typed = tb.call("browser_type", {"ref": ref(text, "input[password]", "Password"), "text": "hunter2"})
    assert "hunter2" not in typed.text and 'value="(hidden)"' in typed.text
    assert typed.page.has_password


def test_a_tool_that_is_slow_becomes_a_timeout_result(session):
    _, tb = session()
    tb.browser.page.set_default_timeout(300)
    tb.browser.page.set_content("<button disabled>Wait</button>")
    text = tb.call("browser_snapshot", {}).text
    result = tb.call("browser_click", {"ref": ref(text, "button", "Wait")})  # a disabled button never becomes clickable
    assert result.timeout and result.error and "TIMEOUT in browser_click" in result.text
    assert [d.klass for d in Detectors().inspect("browser_click", {"ref": 1}, result)] == ["timeout"]
