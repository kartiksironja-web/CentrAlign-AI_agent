"""The look-alike check (gate asks a human before a write that could mean the wrong record) and the human rules.

No model is involved. The gate is the real one with the real company pack; the system's own API is a stand-in
except in the last tests, which use the real mock company.
"""
import pytest

from agent.connectors import ConnectorError, parse_connector
from agent.human import NoHumanReply, ScriptedHuman
from agent.similar import name_words, similar
from agent.toolbox import Toolbox
from tests.agent.helpers import LOOKALIKE, SEED_VENDORS, BASE, form, js, make_gate, vendors_fetch

SUFFIXES = ("pvt", "ltd", "llc", "co")


# ---- the similarity rule -------------------------------------------------

@pytest.mark.parametrize("a, b, expected", [
    ("Acme Supplies Pvt Ltd", "Acme Supplies LLC", True),     # same words once the legal suffixes are ignored
    ("acme  supplies", "ACME SUPPLIES!", True),               # case, spacing and punctuation do not matter
    ("Foo Bar", "Foo Baz", True),                              # one of two words shared: exactly the threshold
    ("Foo Bar Baz", "Foo Qux Quux", False),                    # one of three: below it
    ("Zenith Industrial Ltd", "Acme Supplies Pvt Ltd", False),  # only a suffix in common, and suffixes are ignored
    ("Kaveri Packaging Co", "Zenith Industrial Ltd", False),
    ("Acme", "Acmee", False),                                  # spelling is NOT compared: a documented limit
    ("", "Acme", False), ("Pvt Ltd", "Acme Pvt Ltd", False),   # nothing left to compare
])
def test_similar_names(a, b, expected):
    assert similar(a, b, SUFFIXES) is expected


def test_without_an_ignore_list_a_shared_suffix_counts_as_a_word():
    assert name_words("Acme Supplies Pvt Ltd") == {"acme", "supplies", "pvt", "ltd"}
    assert name_words("Acme Supplies Pvt Ltd", ("PVT", "ltd")) == {"acme", "supplies"}


def test_the_threshold_is_the_manifests_to_set():
    assert similar("Foo Bar Baz", "Foo Qux Quux", (), min_shared=0.3) and not similar("Foo Bar Baz", "Foo Qux Quux", (), 0.5)


# ---- the manifest ---------------------------------------------------------

def connector(**action):
    base = {"id": "x", "name": "X", "base_url": "http://127.0.0.1:1", "description": "d",
            "actions": [{"name": "a", "match": [{"method": "POST", "path": "/a"}], "extract": {"who": "json.who", "n": "json.n"},
                         **action}]}
    return base


ENTITY = {"field": "who", "candidates_url": "/list", "id_field": "id", "name_field": "name"}


def test_a_manifest_can_declare_an_entity_and_optional_fields():
    action = parse_connector(connector(entity=ENTITY, optional_fields=["n"])).actions[0]
    assert action.entity.min_shared == 0.5 and action.entity.ignore_words == () and action.optional_fields == ("n",)


@pytest.mark.parametrize("change, fragment", [
    ({"entity": {**ENTITY, "field": "nope"}}, "not extracted"),
    ({"entity": {**ENTITY, "min_shared": 0}}, "min_shared"),
    ({"entity": {**ENTITY, "min_shared": 1.5}}, "min_shared"),
    ({"entity": {**ENTITY, "typo": 1}}, "typo"),
    ({"entity": {"field": "who"}}, "candidates_url"),
    ({"optional_fields": ["missing"]}, "not extracted"),
])
def test_bad_entity_or_optional_declarations_are_load_errors(change, fragment):
    with pytest.raises(ConnectorError, match=fragment):
        parse_connector(connector(**change))


# ---- the gate ---------------------------------------------------------------

BILL = dict(vendor_id="7", invoice_no="INV-2041", amount="48200", due_date="03/11/2026")
WITH_TWIN = [*SEED_VENDORS, LOOKALIKE]


def test_without_a_look_alike_the_write_goes_through():
    calls = []
    gate = make_gate(fetch=vendors_fetch(SEED_VENDORS, calls))
    assert gate.evaluate(form("/erp/bills/new", **BILL)).allowed
    assert calls == [BASE + "/erp/api/vendors"]  # the gate listed the vendors itself, nothing else


def test_a_look_alike_blocks_the_write_and_the_message_says_what_to_do():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    d = gate.evaluate(form("/erp/bills/new", **BILL))
    assert (d.effect, d.rule_id, d.allowed) == ("require_clarification", "similar_records", False)
    assert d.message.startswith("BLOCKED: clarification required (rule similar_records). Use ask_human(kind=clarification).")
    assert "'Acme Supplies Pvt Ltd' (vendor_id=7) looks like 'Acme Supplies LLC' (vendor_id=8)" in d.message
    assert gate.log[-1]["effect"] == "require_clarification"
    [pending] = gate.clarifications.pending()
    assert pending.key == d.key == "create_bill|vendor_id in {7,8}"


def test_the_api_route_is_blocked_the_same_way_and_the_other_look_alike_too():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    for vendor in (7, 8):
        d = gate.evaluate(js("POST", "/erp/api/bills", {"vendor_id": vendor, "invoice_no": "I", "amount": 100, "due_date": "2026-11-03"}))
        assert d.effect == "require_clarification"
    assert len(gate.clarifications.pending()) == 1  # one question for the group, not one per vendor


def ask(gate, human, kind="clarification", question="Which one?"):
    toolbox = Toolbox(gate, human, default_base_url=BASE)
    return toolbox.call("ask_human", {"kind": kind, "question": question}), toolbox


def test_a_human_answer_after_the_block_releases_it_for_either_look_alike():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    assert gate.evaluate(form("/erp/bills/new", **BILL)).effect == "require_clarification"
    result, toolbox = ask(gate, ScriptedHuman({"clarification": ["The Pune one"]}))
    assert result.text.startswith("HUMAN REPLY: The Pune one") and "may now be repeated" in result.text
    assert gate.evaluate(form("/erp/bills/new", **BILL)).allowed
    assert gate.evaluate(form("/erp/bills/new", **{**BILL, "vendor_id": "8"})).allowed  # the answer covered the group
    assert gate.clarifications.pending() == []


def test_the_human_is_shown_what_the_gate_found_not_only_what_the_model_asks():
    seen = []

    class Spy(ScriptedHuman):
        def ask(self, kind, question, options, context):
            seen.append((question, context))
            return super().ask(kind, question, options, context)

    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    gate.evaluate(form("/erp/bills/new", **BILL))
    ask(gate, Spy({"clarification": ["Pune"]}), question="Quick question")
    question, context = seen[0]
    assert question == "Quick question"
    assert context == ["What the system found: create_bill: 'Acme Supplies Pvt Ltd' (vendor_id=7) looks like "
                       "'Acme Supplies LLC' (vendor_id=8)"]


def test_a_question_answered_before_the_block_does_not_release_it():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    ask(gate, ScriptedHuman({"clarification": ["Fine"]}))                # nothing was waiting yet
    assert gate.evaluate(form("/erp/bills/new", **BILL)).effect == "require_clarification"


@pytest.mark.parametrize("reply", ["", "   ", "\n"])
def test_an_empty_reply_is_not_an_answer(reply):
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    gate.evaluate(form("/erp/bills/new", **BILL))
    result, toolbox = ask(gate, ScriptedHuman({"clarification": [reply]}))
    assert result.error and "EMPTY REPLY" in result.text and toolbox.interactions == []
    assert gate.evaluate(form("/erp/bills/new", **BILL)).effect == "require_clarification"


def test_no_human_means_the_block_stays():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    gate.evaluate(form("/erp/bills/new", **BILL))
    result, toolbox = ask(gate, ScriptedHuman({}))
    assert result.human_unavailable and "no scripted reply left for kind='clarification'" in result.detail
    assert gate.evaluate(form("/erp/bills/new", **BILL)).effect == "require_clarification"


def test_an_approval_answer_does_not_release_a_clarification():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    gate.evaluate(form("/erp/bills/new", **BILL))
    result, _ = ask(gate, ScriptedHuman({"approval": ["approve"]}), kind="approval")
    assert result.error and "NOTHING IS WAITING FOR APPROVAL" in result.text
    assert gate.evaluate(form("/erp/bills/new", **BILL)).effect == "require_clarification"


def test_the_look_alike_question_comes_before_the_approval_question():
    gate = make_gate(fetch=vendors_fetch(WITH_TWIN))
    big = {**BILL, "amount": "80000"}
    assert gate.evaluate(form("/erp/bills/new", **big)).effect == "require_clarification"
    ask(gate, ScriptedHuman({"clarification": ["Pune"]}))
    assert gate.evaluate(form("/erp/bills/new", **big)).effect == "require_approval"


@pytest.mark.parametrize("fetch, rule", [
    (lambda url: (_ for _ in ()).throw(ConnectionError("down")), "lookup_failed"),
    (lambda url: (500, "[]"), "lookup_failed"),
    (lambda url: (200, "not json"), "lookup_failed"),
    (lambda url: (200, '{"not": "a list"}'), "lookup_failed"),
    (lambda url: (200, '[{"id": 7}]'), "lookup_failed"),                 # records without a name
    (vendors_fetch([{"id": 3, "name": "Kaveri"}]), "entity_unknown"),   # the record the request names is not listed
])
def test_if_the_records_cannot_be_listed_or_the_record_is_unknown_the_write_is_denied(fetch, rule):
    d = make_gate(fetch=fetch).evaluate(form("/erp/bills/new", **BILL))
    assert (d.effect, d.rule_id) == ("deny", rule)


def test_a_write_that_names_no_record_is_denied():
    d = make_gate().evaluate(form("/erp/bills/new", invoice_no="I", amount="100"))
    assert (d.effect, d.rule_id) == ("deny", "entity_unknown")


def test_actions_without_an_entity_never_list_anything():
    gate = make_gate(fetch=lambda url: (_ for _ in ()).throw(AssertionError("no lookup expected")))
    assert gate.evaluate(form("/erp/bills/1/edit", amount="24500", due_date="07/09/2026")).allowed


# ---- humans: no default answers ------------------------------------------------

def test_a_scripted_human_never_invents_an_answer():
    human = ScriptedHuman({"approval": ["approve"]})
    with pytest.raises(NoHumanReply, match="no scripted reply left for kind='clarification'"):
        human.ask("clarification", "which?", None, [])
    assert human.ask("approval", "ok?", None, []) == "approve"
    with pytest.raises(NoHumanReply):  # used up: it does not repeat the last answer
        human.ask("approval", "again?", None, [])


# ---- a refused approval leaves the company's data untouched (real mock company) ---------

BIG = {"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000, "due_date": "2026-11-10"}


def count_bills(company):
    return company.query("SELECT COUNT(*) AS n FROM erp_bills")[0]["n"]


@pytest.mark.parametrize("human", [ScriptedHuman({"approval": ["deny"]}), ScriptedHuman({"approval": ["maybe"]}),
                                   ScriptedHuman({})])
def test_a_denied_or_unanswered_approval_leaves_zero_rows_by_either_route(company, human):
    comp = company()
    toolbox = comp.toolbox(human)
    before = count_bills(comp)
    write = {"method": "POST", "url": "/erp/api/bills", "json": BIG}
    assert toolbox.call("http_request", write).text.startswith("BLOCKED: approval required")
    toolbox.call("ask_human", {"kind": "approval", "question": "Please approve"})
    again = toolbox.call("http_request", write)
    assert again.blocked and again.text.startswith("BLOCKED")                         # still not through
    form_write = toolbox.call("http_request", {"method": "POST", "url": "/erp/bills/new", "form": [
        {"name": "vendor_id", "value": "5"}, {"name": "invoice_no", "value": "INV-3107"},
        {"name": "amount", "value": "80000"}, {"name": "due_date", "value": "10/11/2026"}]})
    assert form_write.blocked                                                           # nor by the form route
    assert count_bills(comp) == before


def test_an_approved_over_limit_bill_is_written_exactly_once(company):
    comp = company()
    toolbox = comp.toolbox(ScriptedHuman({"approval": ["approve"]}))
    write = {"method": "POST", "url": "/erp/api/bills", "json": BIG}
    toolbox.call("http_request", write)
    toolbox.call("ask_human", {"kind": "approval", "question": "Please approve"})
    assert toolbox.call("http_request", write).text.startswith("HTTP 201")
    assert toolbox.call("http_request", write).blocked                                  # single-use
    assert count_bills(comp) == 3


def test_the_real_company_blocks_a_write_for_the_duplicate_vendor_fault(company):
    comp = company(faults=["DUPLICATE_VENDOR"])
    toolbox = comp.toolbox(ScriptedHuman({"clarification": ["Pune"]}))
    write = {"method": "POST", "url": "/erp/api/bills",
             "json": {"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"}}
    before = count_bills(comp)
    blocked = toolbox.call("http_request", write)
    assert blocked.blocked and "Acme Supplies LLC" in blocked.text and count_bills(comp) == before
    toolbox.call("ask_human", {"kind": "clarification", "question": "Which Acme?"})
    assert toolbox.call("http_request", write).text.startswith("HTTP 201") and count_bills(comp) == before + 1
