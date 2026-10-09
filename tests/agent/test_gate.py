"""The gate against the real company pack: hosts, deny rules, every action rule, approvals."""
import pytest

from agent.gate import GateRequest
from tests.agent.helpers import BASE, bills_fetch, form, get, js, make_gate, no_fetch


def verdict(gate, request):
    d = gate.evaluate(request)
    return d.effect, d.rule_id


# ---- reads ---------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/", "/portal/vendors", "/portal/invoices?vendor_id=7", "/portal/invoices/INV-2041",
    "/portal/files/INV-2041.pdf", "/erp/bills", "/erp/bills/new", "/erp/api/bills", "/erp/api/bills/2",
])
def test_reads_on_allowed_hosts_pass(path):
    d = make_gate().evaluate(get(path))
    assert d.allowed and d.rule_id is None


def test_head_and_options_are_reads_too():
    gate = make_gate()
    assert gate.evaluate(GateRequest("HEAD", BASE + "/erp/bills")).allowed
    assert gate.evaluate(GateRequest("OPTIONS", BASE + "/erp/bills")).allowed


# ---- hosts ---------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://evil.example/x",
    "http://127.0.0.1:8001/erp/bills",                 # other port
    "https://127.0.0.1:8000/erp/bills",                # other scheme
    "http://localhost:8000/erp/bills",                 # other name for the same machine
    "http://127.0.0.1:8000@evil.example/",             # userinfo trick
    "http://user:pw@127.0.0.1:8000/erp/bills",         # credentials in the URL
    "http://127.0.0.1:8000.evil.example/",
    "file:///C:/Windows/win.ini",
    "data:text/html,hi",
    "javascript:alert(1)",
    "ftp://127.0.0.1/x",
    "/erp/bills",                                      # relative
    "",
    "http://127.0.0.1:99999/x",                        # invalid port
    "http://127.0.0.1:8000/x\r\nHost: evil",           # control characters
])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_hosts_and_odd_urls_are_denied(url, method):
    d = make_gate().evaluate(GateRequest(method, url))
    assert (d.effect, d.rule_id) == ("deny", "host_policy")
    assert d.message.startswith("BLOCKED: denied by host_policy")


def test_the_denial_names_the_allowed_hosts():
    d = make_gate().evaluate(get("/x", base="http://evil.example"))
    assert "http://127.0.0.1:8000" in d.reason and make_gate().allowed_hosts == ["http://127.0.0.1:8000"]


def test_base_urls_follow_the_overrides():
    gate = make_gate(base_urls={"erp": "http://127.0.0.1:9999", "portal": "http://127.0.0.1:9999"})
    assert gate.evaluate(get("/erp/bills", base="http://127.0.0.1:9999")).allowed
    assert gate.evaluate(get("/erp/bills")).rule_id == "host_policy"


# ---- match rules: apply to EVERY method ----------------------------------

ADMIN_PATHS = ["/admin/faults", "/admin/reset", "/admin", "/admin/", "/%61dmin/faults", "/x/../admin/faults",
               "//admin/faults", "/ADMIN/faults", "/portal/..%2Fadmin/faults", "/admin/../admin/faults",
               "/erp/bills/../../admin/reset"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH"])
def test_admin_is_off_limits_for_every_method_and_every_spelling(method, path):
    gate = make_gate()
    d = gate.evaluate(GateRequest(method, BASE + path, "{}", "application/json"))
    assert (d.effect, d.rule_id) == ("deny", "admin_off_limits"), (method, path)


def test_a_backslash_path_is_not_allowed_either():
    d = make_gate().evaluate(GateRequest("GET", BASE + "\\admin\\faults"))
    assert not d.allowed


@pytest.mark.parametrize("path", ["/erp/api/bills/1", "/portal/invoices/INV-2041", "/nothing", "/admin/x"])
def test_delete_is_denied_everywhere(path):
    d = make_gate().evaluate(GateRequest("DELETE", BASE + path))
    assert (d.effect, d.rule_id) == ("deny", "no_deletes")
    assert "never allowed" in d.message


# ---- writes must be mapped -----------------------------------------------

@pytest.mark.parametrize("request_", [
    form("/erp/unknown"),
    form("/portal/vendors"),
    form("/erp/bills/new/extra"),
    js("PUT", "/erp/api/bills", {"amount": 1}),
    js("PUT", "/erp/api/bills/1", {}),                 # carries none of the fields that make it an action
    js("PUT", "/erp/api/bills/1", {"note": "x"}),
    js("PATCH", "/erp/api/bills/1", {"amount": 1}),
])
def test_writes_that_no_connector_maps_are_denied(request_):
    assert verdict(make_gate(), request_) == ("deny", "unmapped_write")


def test_a_write_to_a_read_only_system_is_denied():
    assert verdict(make_gate(), form("/portal/invoices/INV-2041", method="POST")) == ("deny", "unmapped_write")


# ---- create_bill ---------------------------------------------------------

BILL = dict(vendor_id="5", invoice_no="INV-3107")


def test_a_bill_under_the_limit_is_allowed_and_its_fields_are_logged():
    d = make_gate().evaluate(form("/erp/bills/new", amount="48200", due_date="03/11/2026", **BILL))
    assert d.allowed and d.action == "create_bill"
    assert d.fields == {"vendor_id": "5", "invoice_no": "INV-3107", "amount": "48200"}


@pytest.mark.parametrize("amount, effect", [
    ("49999.99", "allow"), ("50000", "allow"), ("50000.00", "allow"), ("50000.01", "require_approval"),
    ("80000", "require_approval"), ("1e5", "require_approval"), (" 80000 ", "require_approval"),
])
def test_the_limit_is_exactly_greater_than_50000(amount, effect):
    assert make_gate().evaluate(form("/erp/bills/new", amount=amount, **BILL)).effect == effect


def test_the_block_message_is_the_one_the_agent_was_promised():
    d = make_gate().evaluate(form("/erp/bills/new", amount="80000", **BILL))
    assert d.message == "BLOCKED: approval required (rule bill_over_limit). Use ask_human(kind=approval)."


@pytest.mark.parametrize("request_", [
    js("POST", "/erp/api/bills", {"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000, "due_date": "2026-11-10"}),
    js("POST", "/erp/api/bills", {"vendor_id": 5, "invoice_no": "INV-3107", "amount": "80000"}),
    js("POST", "/erp/api/bills", {"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000.5}),
])
def test_the_json_api_is_gated_like_the_form(request_):
    assert verdict(make_gate(), request_) == ("require_approval", "bill_over_limit")


def test_the_json_api_under_the_limit_is_allowed():
    d = make_gate().evaluate(js("POST", "/erp/api/bills", {"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200}))
    assert d.allowed and d.action == "create_bill"


@pytest.mark.parametrize("amount", ["nan", "NaN", "inf", "abc", "", "5 5"])
def test_an_unreadable_amount_is_denied_not_waved_through(amount):
    assert verdict(make_gate(), form("/erp/bills/new", amount=amount, **BILL)) == ("deny", "unreadable_request")


def test_a_request_without_the_field_does_not_trigger_the_rule():
    # the receiving system will reject it itself; the gate has nothing to limit
    assert make_gate().evaluate(form("/erp/bills/new", **BILL)).allowed


def test_the_same_field_with_two_different_values_is_ambiguous_and_denied():
    request = GateRequest("POST", BASE + "/erp/bills/new", "amount=100&amount=80000&vendor_id=5",
                          "application/x-www-form-urlencoded")
    assert verdict(make_gate(), request) == ("deny", "unreadable_request")
    same = GateRequest("POST", BASE + "/erp/bills/new", "amount=80000&amount=80000", "application/x-www-form-urlencoded")
    assert verdict(make_gate(), same) == ("require_approval", "bill_over_limit")


@pytest.mark.parametrize("request_", [
    GateRequest("POST", BASE + "/erp/bills/new", "--b\r\nContent-Disposition: form-data; name=amount\r\n\r\n80000\r\n--b--",
                "multipart/form-data; boundary=b"),
    GateRequest("POST", BASE + "/erp/bills/new", "amount=80000"),                       # no content type
    GateRequest("POST", BASE + "/erp/bills/new", "amount=80000", "text/plain"),
    GateRequest("POST", BASE + "/erp/api/bills", "[1, 2]", "application/json"),
    GateRequest("POST", BASE + "/erp/api/bills", "{not json", "application/json"),
    GateRequest("POST", BASE + "/erp/api/bills", b"\xff\xfe", "application/json"),
])
def test_a_write_body_the_gate_cannot_read_is_denied(request_):
    assert verdict(make_gate(), request_) == ("deny", "unreadable_request")


def test_a_write_with_no_body_is_fine_when_the_action_needs_none():
    gate = make_gate(fetch=bills_fetch({"2": 36450}))
    assert gate.evaluate(GateRequest("POST", BASE + "/erp/bills/2/mark_paid")).allowed


# ---- update_bill (the hole closed in Phase 0) ----------------------------

def test_editing_a_bill_cannot_raise_the_amount_past_the_limit_unchecked():
    gate = make_gate()
    assert verdict(gate, form("/erp/bills/1/edit", amount="80000", due_date="07/09/2026")) == ("require_approval", "update_over_limit")
    assert verdict(gate, js("PUT", "/erp/api/bills/1", {"amount": 90000})) == ("require_approval", "update_over_limit")


def test_editing_within_the_limit_or_only_the_due_date_is_allowed():
    gate = make_gate()
    assert gate.evaluate(form("/erp/bills/1/edit", amount="24500", due_date="07/09/2026")).action == "update_bill"
    assert verdict(gate, form("/erp/bills/1/edit", amount="24500", due_date="07/09/2026"))[0] == "allow"
    assert verdict(gate, js("PUT", "/erp/api/bills/1", {"due_date": "2026-09-07"}))[0] == "allow"
    assert gate.log[-1]["fields"] == {"bill_id": "1", "due_date": "2026-09-07"}


# ---- mark_paid with lookup -----------------------------------------------

def test_mark_paid_looks_the_amount_up_because_the_request_has_none():
    calls = []
    gate = make_gate(fetch=bills_fetch({"2": 36450, "3": 80000}, calls))
    assert gate.evaluate(GateRequest("POST", BASE + "/erp/bills/2/mark_paid")).allowed
    d = gate.evaluate(GateRequest("POST", BASE + "/erp/bills/3/mark_paid"))
    assert (d.effect, d.rule_id) == ("require_approval", "paid_over_limit")
    assert d.fields == {"bill_id": "3", "amount": 80000}
    assert calls == [BASE + "/erp/api/bills/2", BASE + "/erp/api/bills/3"]


def test_marking_paid_through_the_api_is_gated_the_same_way():
    gate = make_gate(fetch=bills_fetch({"3": 80000}))
    assert verdict(gate, js("PUT", "/erp/api/bills/3", {"status": "Paid"})) == ("require_approval", "paid_over_limit")


def test_one_request_can_be_two_actions_and_the_strictest_wins():
    gate = make_gate(fetch=bills_fetch({"1": 24500, "3": 80000}))
    # raises the amount AND marks paid: update_over_limit fires
    assert verdict(gate, js("PUT", "/erp/api/bills/1", {"status": "Paid", "amount": 90000})) == ("require_approval", "update_over_limit")
    # small new amount, but the bill itself is large: paid_over_limit fires
    assert verdict(gate, js("PUT", "/erp/api/bills/3", {"status": "Paid", "amount": 100})) == ("require_approval", "paid_over_limit")
    assert verdict(gate, js("PUT", "/erp/api/bills/1", {"status": "Paid", "amount": 100}))[0] == "allow"


@pytest.mark.parametrize("fetch", [
    lambda url: (_ for _ in ()).throw(ConnectionError("down")),    # the system is unreachable
    lambda url: (404, '{"detail": "not found"}'),
    lambda url: (500, "{}"),
    lambda url: (200, "not json"),
    lambda url: (200, "[1, 2]"),
    lambda url: (200, '{"no_amount": 1}'),
])
def test_a_failed_lookup_denies_instead_of_guessing(fetch):
    d = make_gate(fetch=fetch).evaluate(GateRequest("POST", BASE + "/erp/bills/2/mark_paid"))
    assert (d.effect, d.rule_id) == ("deny", "lookup_failed")


def test_a_lookup_returning_an_unreadable_amount_is_denied():
    gate = make_gate(fetch=bills_fetch({"2": "abc"}))
    assert verdict(gate, GateRequest("POST", BASE + "/erp/bills/2/mark_paid")) == ("deny", "unreadable_request")


def test_a_crafted_id_cannot_redirect_the_lookup():
    calls = []
    gate = make_gate(fetch=bills_fetch({}, calls))
    gate.evaluate(GateRequest("POST", BASE + "/erp/bills/1%3Fx=y/mark_paid"))
    assert calls == [BASE + "/erp/api/bills/1%3Fx%3Dy"]  # the id stayed one path segment
    assert verdict(make_gate(), GateRequest("POST", BASE + "/erp/bills/../../admin/mark_paid")) == ("deny", "admin_off_limits")


def test_actions_whose_rules_do_not_need_a_lookup_never_make_one():
    gate = make_gate(fetch=no_fetch)  # no_fetch raises if the gate calls out
    assert gate.evaluate(form("/erp/bills/1/edit", amount="24500", due_date="x")).allowed


# ---- create_vendor -------------------------------------------------------

@pytest.mark.parametrize("terms, effect", [("45", "allow"), ("60", "allow"), ("61", "require_approval"), ("90", "require_approval")])
def test_long_payment_terms_need_approval(terms, effect):
    assert make_gate().evaluate(form("/erp/vendors/new", name="Globex", payment_terms_days=terms)).effect == effect


def test_vendor_rules_via_json_and_bad_values():
    gate = make_gate()
    assert verdict(gate, js("POST", "/erp/api/vendors", {"name": "G", "payment_terms_days": 90})) == ("require_approval", "long_terms")
    assert verdict(gate, js("POST", "/erp/api/vendors", {"name": "G", "payment_terms_days": 30}))[0] == "allow"
    assert verdict(gate, form("/erp/vendors/new", name="G", payment_terms_days="soon")) == ("deny", "unreadable_request")
    assert verdict(gate, form("/erp/vendors/new", name="G"))[0] == "allow"  # no terms given: nothing to limit


# ---- approvals -----------------------------------------------------------

BIG = dict(amount="80000", vendor_id="5", invoice_no="INV-3107")


def test_a_blocked_request_becomes_a_pending_approval_described_by_the_gate():
    gate = make_gate()
    d = gate.evaluate(form("/erp/bills/new", **BIG))
    gate.evaluate(form("/erp/bills/new", **BIG))  # asking twice does not create two entries
    [pending] = gate.approvals.pending()
    assert pending.key == d.key and pending.rule_id == "bill_over_limit"
    assert pending.description == "create_bill: vendor_id=5, invoice_no=INV-3107, amount=80000"


def test_an_approved_request_passes_exactly_once():
    gate = make_gate()
    key = gate.evaluate(form("/erp/bills/new", **BIG)).key
    gate.approvals.decide(key, True)
    assert gate.evaluate(form("/erp/bills/new", **BIG)).allowed
    assert gate.evaluate(form("/erp/bills/new", **BIG)).effect == "require_approval"  # consumed


def test_an_approval_covers_the_action_not_the_channel():
    gate = make_gate()
    key = gate.evaluate(form("/erp/bills/new", **BIG)).key
    gate.approvals.decide(key, True)
    d = gate.evaluate(js("POST", "/erp/api/bills", {"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000.0}))
    assert d.allowed  # same action, same key fields, different channel and number formatting


@pytest.mark.parametrize("other", [
    dict(BIG, amount="80001"), dict(BIG, invoice_no="INV-9999"), dict(BIG, vendor_id="7"),
])
def test_an_approval_does_not_cover_a_different_action(other):
    gate = make_gate()
    gate.approvals.decide(gate.evaluate(form("/erp/bills/new", **BIG)).key, True)
    assert gate.evaluate(form("/erp/bills/new", **other)).effect == "require_approval"


def test_a_denied_request_stays_denied_on_every_channel():
    gate = make_gate()
    gate.approvals.decide(gate.evaluate(form("/erp/bills/new", **BIG)).key, False)
    for request in (form("/erp/bills/new", **BIG),
                    js("POST", "/erp/api/bills", {"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000})):
        d = gate.evaluate(request)
        assert (d.effect, d.rule_id) == ("deny", "bill_over_limit") and "human denied" in d.message


def test_only_pending_keys_can_be_decided():
    with pytest.raises(KeyError):
        make_gate().approvals.decide("made|up|key", True)


def test_an_approval_never_unlocks_a_deny_rule():
    gate = make_gate()
    assert verdict(gate, GateRequest("DELETE", BASE + "/erp/api/bills/1")) == ("deny", "no_deletes")
    assert gate.approvals.pending() == []  # a deny is not something a human can approve


def test_approving_a_vendor_does_not_approve_a_bill():
    gate = make_gate()
    vendor = form("/erp/vendors/new", name="Globex", payment_terms_days="90")
    gate.approvals.decide(gate.evaluate(vendor).key, True)
    assert gate.evaluate(form("/erp/bills/new", **BIG)).effect == "require_approval"
    assert gate.evaluate(vendor).allowed


def test_a_request_needing_two_approvals_waits_for_both():
    gate = make_gate(fetch=bills_fetch({"3": 80000}))
    request = js("PUT", "/erp/api/bills/3", {"status": "Paid", "amount": 90000})  # update_over_limit + paid_over_limit
    first = gate.evaluate(request)
    assert first.effect == "require_approval" and len(gate.approvals.pending()) == 2
    gate.approvals.decide(first.key, True)
    second = gate.evaluate(request)
    assert second.effect == "require_approval" and second.key != first.key
    gate.approvals.decide(second.key, True)
    assert gate.evaluate(request).allowed


# ---- logging -------------------------------------------------------------

def test_every_decision_is_logged_with_its_rule_id_and_no_request_bodies():
    gate = make_gate()
    gate.evaluate(get("/erp/bills"))
    gate.evaluate(form("/erp/bills/new", note="TOPSECRET-123", **BIG))
    gate.evaluate(get("/admin/faults"))
    assert [(e["method"], e["effect"], e["rule_id"]) for e in gate.log] == [
        ("GET", "allow", None), ("POST", "require_approval", "bill_over_limit"), ("GET", "deny", "admin_off_limits"),
    ]
    assert "TOPSECRET-123" not in str(gate.log)  # only the extracted fields are kept


def test_every_rule_in_the_company_policy_fires_in_some_scenario():
    gate = make_gate(fetch=bills_fetch({"3": 80000}))
    for request in (
        form("/erp/bills/new", **BIG),
        form("/erp/bills/1/edit", amount="80000", due_date="07/09/2026"),
        GateRequest("POST", BASE + "/erp/bills/3/mark_paid"),
        form("/erp/vendors/new", name="G", payment_terms_days="90"),
        GateRequest("DELETE", BASE + "/erp/api/bills/1"),
        get("/admin/faults"),
    ):
        gate.evaluate(request)
    fired = {e["rule_id"] for e in gate.log}
    assert fired == {r.id for r in gate.policy.rules}
