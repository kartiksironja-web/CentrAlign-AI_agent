"""The agent loop, driven by a scripted model against the REAL mock company and the REAL auditor.

No LLM is involved, so every run is deterministic. What is tested here is the loop: state,
one tool per step, gate and approvals, detectors and forced hand-over, budgets, finish
acceptance, the audit feedback ladder, the final report and the run log.
"""
import json

import pytest
import yaml

from agent.human import ScriptedHuman
from agent.loop import RunConfig
from agent.runner import Settings, run_task
from tests.agent.fakes import FakeClock, FakeLLM, browser_step, call, prep, response, text, tool_call
from tests.agent.helpers import PACK


@pytest.fixture
def run_loop(company, tmp_path, chromium):
    def run(script, faults=(), human=None, config=None, use_browser=False, clock=None,
            task="Enter Acme Supplies' latest invoice into the ERP and tell me when it's done."):
        comp = company(faults)
        llm = FakeLLM(script)
        result = run_task(task, Settings(
            base_url=comp.url, data_dir=comp.data_dir, repo_root=tmp_path, runs_dir=tmp_path / "runs",
            human=human or ScriptedHuman({}), pack_dir=PACK, use_browser=use_browser,
            chromium=chromium if use_browser else None, llm=llm, config=config or RunConfig(), clock=clock))
        return result, llm, comp
    return run


def assertions_yaml(vendor=7, invoice="INV-2041", rows=1):
    def field(name):
        return {"id": name, "kind": "field_equals",
                "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": invoice}, "field": name},
                "expected": {"from": "query", "db": "portal", "table": "portal_invoices", "where": {"vendor_id": vendor},
                             "order_by": "invoice_date desc", "limit": 1, "field": name}}
    return yaml.safe_dump({"assertions": [
        *[field(f) for f in ("vendor_id", "invoice_no", "amount", "due_date")],
        {"kind": "rows_added", "table": "erp_bills", "count": rows},
        {"kind": "no_other_changes", "except_tables": ["erp_bills"]},
    ]})


GOOD_BILL = dict(method="POST", url="/erp/api/bills",
                 json={"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"})
TRAP_FORM = dict(method="POST", url="/erp/bills/new",
                 form={"vendor_id": "7", "invoice_no": "INV-2041", "amount": "48200", "due_date": "03/11/2026"})


def t1_script(summary="Entered INV-2041 for Acme."):
    return [
        call("http_request", method="GET", url="/portal/invoices?vendor_id=7"),
        call("read_document", source="/portal/files/INV-2041.pdf"),
        call("note", key="latest", value="INV-2041, INR 48,200.00, due 03/11/2026"),
        call("http_request", method="GET", url="/erp/api/bills?invoice_no=INV-2041"),
        call("http_request", **GOOD_BILL),
        call("http_request", method="GET", url="/erp/api/bills?invoice_no=INV-2041"),
        call("self_check", passed=True, detail="re-read the bill and compared it with the document"),
        call("declare_assertions", yaml=assertions_yaml()),
        call("finish", status="done", summary=summary),
    ]


def step_texts(result, tool):
    return [s.result_text for s in result.state.steps if s.tool == tool]


# ---- the happy path ----------------------------------------------------------------------

def test_a_complete_run_is_verified_and_reported_from_observed_facts(run_loop):
    result, llm, comp = run_loop(prep("vendor_invoice_entry") + t1_script())
    assert result.outcome == "verified_done" and result.metrics["steps"] == 9
    assert comp.query("SELECT vendor_id, amount, due_date FROM erp_bills WHERE invoice_no = 'INV-2041'") == [
        {"vendor_id": 7, "amount": 48200.0, "due_date": "2026-11-03"}]
    report = result.report
    assert "OUTCOME: DONE and VERIFIED" in report
    assert "PASS  due_date: erp_bills.due_date = '2026-11-03', matches the independent source" in report
    assert "PASS  rows_added_5: rows_added on erp_bills: 1 (expected 1)" in report
    assert "PASS  no_other_changes_6: nothing changed beyond the declared changes" in report
    assert "erp.erp_bills: 1 added" in report and "+ {" in report and '"invoice_no": "INV-2041"' in report
    assert "Entered INV-2041 for Acme." in report and "NOT verified" in report  # the agent's words are labelled
    assert "Needs attention:\n  nothing" in report
    assert not llm.script  # every scripted answer was used, and no call was made beyond the script


def test_the_model_sees_state_not_a_growing_transcript(run_loop):
    result, llm, _ = run_loop(prep("vendor_invoice_entry") + t1_script())
    step_prompts = [c["user"] for c in llm.calls[3:]]
    assert all(p.startswith("# Request") and p.endswith("Call exactly one tool now.") for p in step_prompts)
    last = step_prompts[-1]
    assert last.count("## Step ") == 3 and "step 1: " in last  # three verbatim, the rest as one-liners
    assert "latest: INV-2041" in step_prompts[4] and "(none yet" in step_prompts[0]  # notes persist; first step has no history
    assert "MOST RECENT invoice" in step_prompts[0] and "Internal ERP" in step_prompts[0]  # rules and systems are in every prompt
    sizes = [len(p) for p in step_prompts]
    assert max(sizes) < 3 * min(sizes) + 12000  # bounded, not cumulative
    assert llm.calls[3]["system"].startswith("You are an AI employee")
    assert llm.calls[3]["tools"][0]["function"]["parameters"]["required"][0] == "reason"
    names = [t["function"]["name"] for t in llm.calls[3]["tools"]]
    assert names[-4:] == ["update_plan", "self_check", "declare_assertions", "finish"] and len(names) == 13


def test_the_run_log_contains_everything_needed_to_explain_the_run(run_loop, tmp_path):
    result, llm, _ = run_loop(prep("vendor_invoice_entry") + t1_script())
    log = json.loads((tmp_path / "runs" / result.run_id / "run.json").read_text(encoding="utf-8"))
    assert {"config", "request", "contract", "sop_ids", "plans", "steps", "self_checks", "assertions", "audit_attempts",
            "interactions", "gate_log", "notes", "outcome", "metrics", "changes", "report", "llm_calls"} <= set(log)
    assert log["config"]["model"] == "fake/scripted" and log["config"]["audit_mode"] == "enforce" and log["config"]["max_steps"] == 60
    assert log["sop_ids"] == ["vendor_invoice_entry"] and log["plans"][0]["version"] == 1
    step = log["steps"][4]
    assert step["tool"] == "http_request" and step["reason"] == "because" and step["args"]["url"] == "/erp/api/bills"
    assert step["gate"][0]["action"] == "create_bill" and step["gate"][0]["effect"] == "allow" and step["tokens_in"] == 1000 and "ms" in step
    assert log["audit_attempts"][0]["passed"] is True and len(log["audit_attempts"][0]["detail"]["checks"]) == 6
    assert log["self_checks"] == [{"step": 7, "passed": True, "detail": "re-read the bill and compared it with the document"}]
    assert log["outcome"]["outcome"] == "verified_done" and log["metrics"]["first_claim"]["audit_passed"] is True
    assert log["metrics"]["tokens_in"] == 1000 * len(llm.calls) and log["notes"] == {"latest": "INV-2041, INR 48,200.00, due 03/11/2026"}
    assert "API_KEY" not in json.dumps(log) and "ADMIN_TOKEN" not in json.dumps(log)  # no key names, no secrets


# ---- verification: the audit decides --------------------------------------------------------

def date_trap_claim():
    return [
        call("http_request", **TRAP_FORM),
        call("self_check", passed=True, detail="the ERP said Bill saved"),
        call("declare_assertions", yaml=assertions_yaml()),
        call("finish", status="done", summary="Done: the ERP said Bill saved."),
    ]


def test_a_false_completion_is_rejected_with_feedback_then_repaired_and_verified(run_loop):
    script = prep("vendor_invoice_entry") + date_trap_claim() + [
        call("http_request", method="PUT", url="/erp/api/bills/3", json={"due_date": "2026-11-03"}),
        call("finish", status="done", summary="Fixed the due date."),
    ]
    result, llm, comp = run_loop(script, faults=["DATE_TRAP"])
    assert result.outcome == "verified_done"
    rejected = step_texts(result, "finish")[0]
    assert rejected.startswith("FINISH REJECTED: the independent audit failed.") and "AUDIT FAILED (feedback round 1)" in rejected
    assert "due_date (field_equals): field 'due_date' of table 'erp_bills'" in rejected
    assert "2026-03-11" not in rejected and "2026-11-03" not in rejected  # round 1: no values
    assert result.metrics["first_claim"] == {"step": 4, "status": "done", "audit_passed": False}
    assert [a["passed"] for a in result.state.audit_attempts] == [False, True]
    assert "the agent's FIRST claim of success was wrong" in result.report
    assert comp.query("SELECT due_date FROM erp_bills WHERE id = 3") == [{"due_date": "2026-11-03"}]


def test_after_two_failed_rounds_the_run_is_escalated_to_a_human(run_loop):
    script = prep("vendor_invoice_entry") + date_trap_claim() + [
        call("finish", status="done", summary="Still done."), call("finish", status="done", summary="Really done."),
    ]
    result, llm, _ = run_loop(script, faults=["DATE_TRAP"])
    assert result.outcome == "audit_failed" and not llm.script
    first, second, third = step_texts(result, "finish")
    assert "feedback round 1" in first and "feedback round 2" in second
    assert "expected: '2026-11-03'   actual: '2026-03-11'" in second  # round 2 adds the values
    assert "handed to a human" in third
    assert "OUTCOME: NOT VERIFIED: the agent claimed" in result.report and "(the audit failed 3 times)" in result.report
    assert "FAIL  due_date: erp_bills.due_date is '2026-03-11', the source says '2026-11-03'" in result.report


def test_in_advisory_mode_the_first_claim_is_final_and_the_audit_is_only_recorded(run_loop):
    result, llm, _ = run_loop(prep("vendor_invoice_entry") + date_trap_claim(), faults=["DATE_TRAP"],
                              config=RunConfig(audit_mode="advisory"))
    assert result.outcome == "false_completion" and not llm.script
    assert step_texts(result, "finish") == ["Finished."]  # the agent was told nothing about the audit
    assert result.state.audit_attempts[0]["shown_to_agent"] is False and result.state.audit_attempts[0]["passed"] is False
    assert "advisory mode" in result.report and "FAIL  due_date" in result.report


def test_advisory_mode_with_a_correct_claim_is_verified(run_loop):
    result, _, _ = run_loop(prep("vendor_invoice_entry") + t1_script(), config=RunConfig(audit_mode="advisory"))
    assert result.outcome == "verified_done" and result.metrics["first_claim"]["audit_passed"] is True


def existing_record_assertions():
    """'Already done' is verified by checking the EXISTING bill against the source, not by trusting 'it exists'."""
    def field(name):
        return {"id": name, "kind": "field_equals",
                "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": "INV-2039"}, "field": name},
                "expected": {"from": "query", "db": "portal", "table": "portal_invoices", "where": {"invoice_no": "INV-2039"}, "field": name}}
    return yaml.safe_dump({"assertions": [*[field(f) for f in ("vendor_id", "invoice_no", "amount", "due_date")],
                                          {"kind": "rows_added", "table": "erp_bills", "count": 0}, {"kind": "no_other_changes"}]})


def test_no_action_needed_is_also_audited_against_the_existing_record(run_loop):
    script = prep("vendor_invoice_entry") + [
        call("http_request", method="GET", url="/erp/api/bills?invoice_no=INV-2039"),
        call("self_check", passed=True, detail="the bill exists and matches"),
        call("declare_assertions", yaml=existing_record_assertions()),
        call("finish", status="no_action_needed", summary="Already entered."),
    ]
    result, _, _ = run_loop(script)
    assert result.outcome == "verified_no_action" and "NO ACTION NEEDED, verified" in result.report
    assert "PASS  amount: erp_bills.amount = 36450.0" in result.report and "nothing was added, changed or removed" in result.report


def test_no_action_needed_cannot_skip_the_required_checks(run_loop):
    nothing_to_check = yaml.safe_dump({"assertions": [{"kind": "rows_added", "table": "erp_bills", "count": 0}, {"kind": "no_other_changes"}]})
    script = prep("vendor_invoice_entry") + [call("declare_assertions", yaml=nothing_to_check),
                                             call("finish", status="no_action_needed", summary="trust me"),
                                             call("finish", status="blocked", summary="gave up")]
    result, _, _ = run_loop(script)
    assert "required field erp_bills.amount has no field_equals assertion" in result.state.steps[0].result_text
    assert result.state.steps[1].result_text.startswith("FINISH REJECTED: call declare_assertions first")


def test_finish_is_rejected_without_assertions_or_with_invalid_ones(run_loop):
    weak = yaml.safe_load(assertions_yaml())
    weak["assertions"][2] = {"id": "amount", "kind": "field_equals",
                             "actual": weak["assertions"][2]["actual"], "expected": {"from": "literal", "value": 48200}}
    script = prep("vendor_invoice_entry") + [
        call("http_request", **GOOD_BILL),
        call("finish", status="done", summary="done?"),                      # no assertions yet
        call("declare_assertions", yaml=yaml.safe_dump(weak)),               # a literal for a required field
        call("declare_assertions", yaml="assertions: [{kind: nonsense}]"),   # malformed
        call("finish", status="done", summary="done anyway"),                # still nothing declared
        call("declare_assertions", yaml=assertions_yaml()),
        call("finish", status="done", summary="now properly"),
    ]
    result, _, _ = run_loop(script)
    texts = [s.result_text for s in result.state.steps]
    assert texts[1].startswith("FINISH REJECTED: call declare_assertions first")
    assert "ASSERTIONS NOT ACCEPTED" in texts[2] and "independent source" in texts[2]
    assert "ASSERTIONS NOT ACCEPTED (format)" in texts[3] and "'kind' must be one of" in texts[3]
    assert texts[4].startswith("FINISH REJECTED: call declare_assertions first")
    assert result.outcome == "verified_done" and len(result.state.assertion_versions) == 1


def test_revised_assertions_are_all_logged_and_flagged_for_review(run_loop):
    script = prep("vendor_invoice_entry") + [call("http_request", **GOOD_BILL), call("declare_assertions", yaml=assertions_yaml()),
                                             call("declare_assertions", yaml=assertions_yaml()), call("finish", status="done", summary="x")]
    result, _, _ = run_loop(script)
    assert len(result.state.assertion_versions) == 2 and result.metrics["assertion_versions"] == 2
    assert "declared its assertions 2 times" in result.report


# ---- ambiguity, gate and approvals ------------------------------------------------------------

def test_open_questions_block_everything_but_asking_the_requester(run_loop):
    script = prep("vendor_invoice_entry", ambiguities=["Which Acme is meant?"]) + [
        call("http_request", **GOOD_BILL),                                    # a write before asking
        call("http_request", method="GET", url="/portal/vendors"),            # even a read
        call("ask_human", kind="approval", question="may I?"),                # the wrong kind of question
        call("ask_human", kind="clarification", question="Which Acme: Pune or Dubai?", options=["Pune", "Dubai"]),
        call("http_request", method="GET", url="/portal/vendors"),
        call("finish", status="needs_human", summary="stopped for the test"),
    ]
    result, llm, comp = run_loop(script, human=ScriptedHuman({"clarification": ["Pune"]}))
    kinds = [s.kind for s in result.state.steps]
    assert kinds[:5] == ["refused", "refused", "refused", "tool", "tool"]
    assert "Ask them with ask_human(kind=clarification) first" in result.state.steps[0].result_text
    assert "HUMAN REPLY: Pune" in result.state.steps[3].result_text and result.state.steps[4].result_text.startswith("HTTP 200")
    assert [e for e in result.state.steps[0].gate] == []  # the refused write never even reached the gate
    assert "YOU MUST FIRST ask the requester" in llm.calls[3]["user"] and "YOU MUST FIRST" not in llm.calls[7]["user"]
    assert len(comp.query("SELECT * FROM erp_bills")) == 2
    assert "1 human answer(s) came from a SCRIPTED human" in result.report


def test_an_identical_write_sent_twice_is_flagged_in_the_loop(run_loop):
    script = prep("vendor_invoice_entry") + [
        call("http_request", **GOOD_BILL),
        call("http_request", **GOOD_BILL),     # exactly the same write again (the ERP will refuse the duplicate)
        call("finish", status="blocked", summary="stop for the test"),
    ]
    result, _, _ = run_loop(script)
    first, second = result.state.steps[0], result.state.steps[1]
    assert not any("exactly this POST" in m for m in first.detectors)
    assert any("exactly this POST /erp/api/bills" in m for m in second.detectors) and "retry 1 of 2" in second.detectors[0]
    assert "body_digest" in second.gate[0] and second.gate[0]["body_digest"] == first.gate[0]["body_digest"] != ""


def test_when_nobody_can_answer_the_open_questions_the_agent_can_still_hand_over(run_loop):
    """Seen in a real run with no one at the keyboard: 'ask first' stayed in force, so finish was refused too."""
    script = prep("vendor_invoice_entry", ambiguities=["Which Acme is meant?"]) + [
        call("ask_human", kind="clarification", question="Which Acme: Pune or Dubai?"),
        call("finish", status="needs_human", summary="a human has to say which Acme"),
    ]
    result, _, _ = run_loop(script, human=ScriptedHuman({}))
    assert [s.kind for s in result.state.steps] == ["tool", "control"]
    assert "HUMAN UNAVAILABLE" in result.state.steps[0].result_text
    assert result.outcome == "needs_human" and "a human was needed but no answer was available" in result.report


BIG = dict(method="POST", url="/erp/api/bills",
           json={"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000, "due_date": "2026-11-10"})


def test_an_over_limit_bill_is_blocked_approved_by_a_human_and_then_verified(run_loop):
    script = prep("vendor_invoice_entry") + [
        call("http_request", **BIG),
        call("ask_human", kind="approval", question="Please approve this bill"),
        call("http_request", **BIG),
        call("self_check", passed=True, detail="ok"),
        call("declare_assertions", yaml=assertions_yaml(vendor=5, invoice="INV-3107")),
        call("finish", status="done", summary="Entered with approval."),
    ]
    result, _, comp = run_loop(script, human=ScriptedHuman({"approval": ["approve"]}))
    texts = [s.result_text for s in result.state.steps]
    assert texts[0] == "BLOCKED: approval required (rule bill_over_limit). Use ask_human(kind=approval)."
    assert texts[1].startswith("APPROVED by the human: create_bill: vendor_id=5, invoice_no=INV-3107, amount=80000")
    assert texts[2].startswith("HTTP 201")
    assert result.outcome == "verified_done" and result.metrics["gate_blocks"] == 1 and result.metrics["human_interactions"] == 1
    assert comp.query("SELECT amount FROM erp_bills WHERE invoice_no = 'INV-3107'") == [{"amount": 80000.0}]
    # only the write the gate let through is recorded, taken from the gate's own log
    assert [(w["step"], w["method"], w["path"], w["action"]) for w in result.state.writes_made] == [(3, "POST", "/erp/api/bills", "create_bill")]


def test_a_denied_approval_means_no_write_and_a_hand_over(run_loop):
    script = prep("vendor_invoice_entry") + [
        call("http_request", **BIG),
        call("ask_human", kind="approval", question="Please approve this bill"),
        call("http_request", **BIG),                                          # trying again anyway
        call("finish", status="needs_human", summary="The approver said no."),
    ]
    result, _, comp = run_loop(script, human=ScriptedHuman({"approval": ["deny"]}))
    assert "DENIED by the human" in result.state.steps[1].result_text
    assert result.state.steps[2].result_text.startswith("BLOCKED: denied by bill_over_limit") and "human denied" in result.state.steps[2].result_text
    assert result.outcome == "needs_human" and len(comp.query("SELECT * FROM erp_bills")) == 2
    assert "nothing was added, changed or removed" in result.report and "approval request(s) were not approved" in result.report
    assert "no audit was run" in result.report
    assert result.state.writes_made == []  # blocked and denied writes are not "writes you have sent"


def test_asking_for_approval_when_nothing_is_blocked_does_not_conjure_one(run_loop):
    script = prep() + [call("ask_human", kind="approval", question="may I write anything I like?"),
                       call("finish", status="blocked", summary="x")]
    result, _, _ = run_loop(script, human=ScriptedHuman({"approval": ["approve"]}))
    assert "NOTHING IS WAITING FOR APPROVAL" in result.state.steps[0].result_text and result.metrics["human_interactions"] == 0


def test_a_missing_human_forces_a_hand_over(run_loop):
    script = prep() + [call("ask_human", kind="clarification", question="anyone there?"),
                       call("http_request", method="GET", url="/portal/vendors"),
                       call("finish", status="needs_human", summary="nobody answered")]
    result, _, _ = run_loop(script)  # a scripted human with no replies at all
    assert "HUMAN UNAVAILABLE" in result.state.steps[0].result_text
    assert result.state.steps[1].kind == "refused" and "No human can answer" in result.state.steps[1].result_text
    assert result.outcome == "needs_human" and "a human was needed but no answer was available" in result.report


# ---- detectors, budgets, protocol ----------------------------------------------------------------

def test_a_used_up_retry_budget_forces_asking_a_human_or_finishing(run_loop):
    script = prep() + [
        call("browser_goto", url="/a"), call("browser_goto", url="/b"), call("browser_goto", url="/c"),  # no browser: tool errors
        call("http_request", method="GET", url="/portal/vendors"),       # refused now
        call("finish", status="blocked", summary="the browser is not available"),
    ]
    result, llm, _ = run_loop(script)
    assert [s.kind for s in result.state.steps] == ["tool", "tool", "tool", "refused", "control"]  # finish is a control step
    assert "[DETECTOR] tool_error:" in result.state.steps[0].result_text and "retry 1 of 2" in result.state.steps[0].result_text
    assert "retry 2 of 2" in result.state.steps[1].result_text and "ask_human or finish with status blocked" in result.state.steps[2].result_text
    assert "The retry budget for 'tool_error' problems is used up" in result.state.steps[3].result_text
    assert "# NOTICE" in llm.calls[6]["user"] and result.outcome == "blocked"
    assert "retry budget used up for: tool_error" in result.report


def test_a_save_that_does_nothing_is_noticed_and_the_agent_switches_to_the_api(run_loop):
    script = prep("vendor_invoice_entry") + [
        call("browser_goto", url="/erp/bills/new"),
        browser_step("browser_select", "select", "Vendor", value="Acme Supplies Pvt Ltd (Pune)"),
        browser_step("browser_type", "input[text]", "Invoice number", text="INV-2041"),
        browser_step("browser_type", "input[number]", "Amount", text="48200"),
        browser_step("browser_type", "input[text]", "Due date", text="03/11/2026"),
        browser_step("browser_click", "button", "Save Bill"),
        call("http_request", **GOOD_BILL),
        call("self_check", passed=True, detail="read back"),
        call("declare_assertions", yaml=assertions_yaml()),
        call("finish", status="done", summary="Saved through the API because the Save button did nothing."),
    ]
    result, llm, comp = run_loop(script, faults=["SAVE_BROKEN"], use_browser=True)
    click = result.state.steps[5]
    assert click.detectors and click.detectors[0].startswith("[DETECTOR] noop") and "[DETECTOR] noop" in llm.calls[9]["user"]
    assert result.outcome == "verified_done" and result.metrics["detector_counts"]["noop"] == 1
    assert comp.query("SELECT created_via FROM erp_bills WHERE invoice_no = 'INV-2041'") == [{"created_via": "api"}]


def test_the_step_limit_ends_the_run_whatever_the_model_thinks(run_loop):
    script = prep() + [call("note", key=f"k{i}", value="v") for i in range(3)]
    result, llm, _ = run_loop(script, config=RunConfig(max_steps=3))
    assert result.outcome == "budget_exhausted" and result.metrics["steps"] == 3 and not llm.script
    assert "all 3 steps were used" in result.report and "NOT COMPLETED" in result.report


def test_the_time_limit_ends_the_run(run_loop):
    clock = FakeClock()
    slow = lambda system, user, tools: (clock.advance(200), call("note", key="k", value="v"))[1]
    result, _, _ = run_loop(prep() + [slow, slow, slow], clock=clock, config=RunConfig(max_seconds=300))
    assert result.outcome == "budget_exhausted" and result.metrics["steps"] == 2
    assert "the 300 s time limit was reached" in result.report


def test_the_cost_cap_ends_the_run(run_loop):
    script = prep() + [call("note", key=f"k{i}", value="v", cost=0.4) for i in range(5)]
    result, _, _ = run_loop(script, config=RunConfig(max_cost_usd=1.0))
    assert result.outcome == "budget_exhausted" and "cost limit of $1.00" in result.report and result.metrics["steps"] == 3


def test_the_budget_line_warns_when_steps_run_low(run_loop):
    script = prep() + [call("note", key=f"k{i}", value="v") for i in range(6)]
    _, llm, _ = run_loop(script, config=RunConfig(max_steps=6))
    assert "step 1 of 6" in llm.calls[3]["user"] and "VERY FEW STEPS LEFT" not in llm.calls[3]["user"]
    assert "VERY FEW STEPS LEFT" in llm.calls[-1]["user"]


def test_answering_without_a_tool_call_is_nudged_and_then_ends_the_run(run_loop):
    script = prep() + [text("Sure, I will do that.")] * 4
    result, _, _ = run_loop(script, config=RunConfig(max_nudges=3))
    assert [s.kind for s in result.state.steps] == ["nudge"] * 4 and result.outcome == "blocked"
    assert "(You wrote: 'Sure, I will do that.')" in result.state.steps[0].result_text
    assert "the model would not follow the one-tool-call protocol" in result.report


def test_only_the_first_of_several_tool_calls_runs(run_loop):
    two = response(tool_call("note", key="first", value="1"), tool_call("note", key="second", value="2"))
    result, _, _ = run_loop(prep() + [two, call("finish", status="blocked", summary="x")])
    assert result.state.notes.items() == {"first": "1"}
    assert "You made 2 tool calls in one step; only the first was run" in result.state.steps[0].result_text


def test_a_call_without_a_reason_is_rejected_and_not_executed(run_loop):
    result, _, comp = run_loop(prep() + [call("http_request", None, **GOOD_BILL), call("finish", status="blocked", summary="x")])
    assert result.state.steps[0].kind == "invalid" and "needs a one-sentence `reason`" in result.state.steps[0].result_text
    assert len(comp.query("SELECT * FROM erp_bills")) == 2


def test_malformed_arguments_are_reported_back(run_loop):
    from agent.llm import ToolCall
    bad = response(ToolCall("note", {}, "arguments are not valid JSON", "{oops"))
    result, _, _ = run_loop(prep() + [bad, call("finish", status="blocked", summary="x")])
    assert "INVALID ARGUMENTS for note: arguments are not valid JSON" in result.state.steps[0].result_text


def test_unknown_tools_and_wrong_arguments_are_errors_the_model_can_read(run_loop):
    result, _, _ = run_loop(prep() + [call("rm_rf"), call("http_request", method="GET"), call("finish", status="blocked", summary="x")])
    assert "UNKNOWN TOOL 'rm_rf'" in result.state.steps[0].result_text
    assert "INVALID ARGUMENTS for http_request" in result.state.steps[1].result_text


def test_repeated_refusals_end_the_run(run_loop):
    script = prep(ambiguities=["q?"]) + [call("http_request", method="GET", url="/portal/vendors")] * 4
    result, _, _ = run_loop(script, config=RunConfig(max_refusals=3))
    assert result.outcome == "blocked" and "kept calling tools that are not allowed" in result.report


# ---- the plan and set-up -----------------------------------------------------------------------------

def test_plan_changes_are_versioned_with_a_reason(run_loop):
    plan = [{"text": "read the sources", "done": True}, {"text": "do the work"}]
    result, llm, _ = run_loop(prep() + [call("update_plan", items=plan, change_reason="read everything"),
                                        call("finish", status="blocked", summary="x")])
    assert [(p.version, p.reason, p.step) for p in result.state.plans] == [(1, "initial plan", 0), (2, "read everything", 1)]
    assert "# Plan (version 2)\n[x] read the sources\n[ ] do the work" in llm.calls[4]["user"]
    assert "plan v2: read everything" in result.state.steps[0].summary


def test_the_set_up_prompts_carry_the_request_the_systems_and_the_rule_index(run_loop):
    result, llm, _ = run_loop(prep("vendor_invoice_entry") + [call("finish", status="blocked", summary="x")])
    contract_prompt, select_prompt, plan_prompt = (llm.calls[i]["user"] for i in range(3))
    assert "Enter Acme Supplies' latest invoice" in contract_prompt and "Internal ERP" in contract_prompt
    assert "- vendor_invoice_entry: Entering a vendor invoice into the ERP as a bill." in contract_prompt
    assert "ONLY questions that cannot be answered by looking" in contract_prompt
    assert "Your contract:" in select_prompt and "Company rules that apply" in plan_prompt and "MOST RECENT" in plan_prompt
    assert result.state.sops[0].id == "vendor_invoice_entry"


def test_an_unknown_rule_id_is_rejected_and_the_model_gets_to_correct_it(run_loop):
    script = [call("submit_contract", None, goal="g"), call("select_rules", None, sop_ids=["made_up"], reason="x"),
              call("select_rules", None, sop_ids=["vendor_invoice_entry"], reason="x"), call("submit_plan", None, items=["a"]),
              call("finish", status="blocked", summary="x")]
    result, llm, _ = run_loop(script)
    assert "unknown rule ids ['made_up']" in llm.calls[2]["user"] and result.state.sops[0].id == "vendor_invoice_entry"


def test_a_model_that_cannot_produce_a_contract_ends_the_run_with_an_error(run_loop):
    result, _, _ = run_loop([call("submit_contract", None)] * 3)
    assert result.outcome == "error" and "submit_contract" in result.report and result.metrics["steps"] == 0


def test_blocked_and_needs_human_finishes_do_not_need_assertions_but_report_what_changed(run_loop):
    script = prep("vendor_invoice_entry") + [call("http_request", **GOOD_BILL),
                                             call("finish", status="needs_human", summary="I made a bill but I am unsure")]
    result, _, _ = run_loop(script)
    assert result.outcome == "needs_human" and "erp.erp_bills: 1 added" in result.report
    assert "no audit was run" in result.report


def test_declared_assertions_are_audited_for_the_record_when_the_agent_hands_over(run_loop):
    script = prep("vendor_invoice_entry") + [call("http_request", **TRAP_FORM), call("declare_assertions", yaml=assertions_yaml()),
                                             call("finish", status="needs_human", summary="not sure it is right")]
    result, _, _ = run_loop(script, faults=["DATE_TRAP"])
    assert result.outcome == "needs_human"
    assert "FAIL  due_date: erp_bills.due_date is '2026-03-11', the source says '2026-11-03'" in result.report
    assert result.state.audit_attempts[-1]["shown_to_agent"] is False
