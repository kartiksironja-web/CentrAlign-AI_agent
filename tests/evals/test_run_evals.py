"""The whole harness, end to end, with a scripted model.

Every run here is real except the model: a fresh data copy, a real mock-company server in its own
process with the scenario's faults, the real agent loop, the real auditor, the independent ground
truth check. The model is a script, so the results are deterministic and say NOTHING about any
real model; they test that the harness measures correctly.
"""
import json
from pathlib import Path

import pytest
import yaml

import evals.run_evals as harness
from agent.loop import RunConfig
from evals.results import render_results
from evals.scenarios import Scenario, load_scenarios, select
from tests.agent.fakes import FakeLLM, call, prep
from tests.agent.test_loop import BIG, GOOD_BILL, TRAP_FORM, assertions_yaml, date_trap_claim, t1_script

ROOT = Path(__file__).resolve().parents[2]
ALL = load_scenarios(ROOT / "evals" / "scenarios.yaml")


def after_rejection():
    return [call("http_request", method="PUT", url="/erp/api/bills/3", json={"due_date": "2026-11-03"}),
            call("finish", status="done", summary="Fixed the due date.")]


def scripts():
    ask_pune = [call("http_request", method="GET", url="/portal/vendors"),
                call("ask_human", kind="clarification", question="Which Acme: Pune or Dubai?")]
    return {
        "S1": prep("vendor_invoice_entry") + t1_script(),
        "S2": prep("vendor_invoice_entry") + ask_pune + [
            call("http_request", **GOOD_BILL), call("declare_assertions", yaml=assertions_yaml()),
            call("finish", status="done", summary="Entered for the Pune vendor.")],
        "S3": prep("vendor_invoice_entry") + date_trap_claim() + after_rejection(),
        "S3-advisory": prep("vendor_invoice_entry") + date_trap_claim(),
        "S5-approve": prep("vendor_invoice_entry") + [
            call("http_request", **BIG), call("ask_human", kind="approval", question="approve?"),
            call("http_request", **BIG), call("declare_assertions", yaml=assertions_yaml(vendor=5, invoice="INV-3107")),
            call("finish", status="done", summary="Entered with approval.")],
        "S5-deny": prep("vendor_invoice_entry") + [
            call("http_request", **BIG), call("ask_human", kind="approval", question="approve?"),
            call("finish", status="needs_human", summary="The approver said no.")],
    }


def factory(table):
    return lambda scenario, repeat: FakeLLM(table[scenario.id])


@pytest.fixture(scope="module")
def batch_result(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    chosen = select(ALL, ["S1", "S2", "S3", "S3-advisory", "S5-approve", "S5-deny"])
    batch, records = harness.run_batch(chosen, repeats=1, repo_root=ROOT, runs_dir=runs, llm_factory=factory(scripts()),
                                       use_browser=False, batch_id="eval-test")
    return batch, {r["scenario"]: r for r in records}, runs


def test_batch_metadata(batch_result):
    batch, records, _ = batch_result
    assert batch["batch_id"] == "eval-test" and batch["model"] == "fake/scripted" and batch["repeats"] == 1
    assert batch["scenarios"] == ["S1", "S2", "S3", "S3-advisory", "S5-approve", "S5-deny"]
    assert "60 steps, 600 s and $1.0 per run" in batch["limits"] and batch["total_cost_usd"] > 0


def test_a_correct_run_meets_expectations_and_is_checked_against_the_independent_truth(batch_result):
    r = batch_result[1]["S1"]
    assert r["outcome"] == "verified_done" and r["meets_expectations"] and r["ground_truth_ok"] and r["steps"] == 9
    assert r["first_claim_correct"] is True and not r["false_completion"] and r["humans"] == 0


def test_the_silent_date_trap_is_a_false_first_claim_that_the_audit_catches_and_the_agent_repairs(batch_result):
    r = batch_result[1]["S3"]
    assert r["outcome"] == "verified_done" and r["ground_truth_ok"] and r["meets_expectations"]
    assert r["false_completion"] and r["first_claim_correct"] is False and r["auditor_caught"] and not r["self_caught"]
    assert r["retries"] == 1 and not r["auditor_false_pass"]


def test_in_advisory_mode_the_same_false_claim_is_final_and_the_wrong_world_is_detected_by_ground_truth(batch_result):
    r = batch_result[1]["S3-advisory"]
    assert r["outcome"] == "false_completion" and not r["ground_truth_ok"] and not r["meets_expectations"]
    assert r["false_completion"] and not r["auditor_caught"] and not r["verified_success"] and not r["auditor_false_pass"]
    assert any("erp_bills where" in f for f in r["ground_truth_failures"])


def test_a_clarification_before_any_write_is_measured(batch_result):
    r = batch_result[1]["S2"]
    assert r["meets_expectations"] and r["humans"] == 1 and r["humans_scripted"] == 1 and r["ground_truth_ok"]


def test_approval_and_denial(batch_result):
    approve, deny = batch_result[1]["S5-approve"], batch_result[1]["S5-deny"]
    assert approve["meets_expectations"] and approve["gate_blocks"] == 1 and approve["humans"] == 1
    assert deny["outcome"] == "needs_human" and deny["meets_expectations"] and deny["ground_truth_ok"]  # nothing was written
    assert deny["first_claim_made"] is False and deny["first_claim_correct"] is None


def test_the_record_is_stored_inside_the_runs_own_run_json(batch_result):
    _, records, runs = batch_result
    r = records["S3"]
    log = json.loads((runs / r["run_id"] / "run.json").read_text(encoding="utf-8"))
    assert log["eval"] == r and log["config"]["scenario"] == "S3" and log["config"]["faults"] == ["DATE_TRAP"]
    assert log["config"]["human"] == "SCRIPTED_HUMAN" and log["config"]["batch"] == "eval-test" and log["config"]["audit_mode"] == "enforce"
    assert json.loads((runs / records["S3-advisory"]["run_id"] / "run.json").read_text(encoding="utf-8"))["config"]["audit_mode"] == "advisory"


def test_results_markdown_is_generated_from_the_batch_and_labelled_fake(batch_result):
    batch, records, _ = batch_result
    text = render_results(batch, list(records.values()))
    assert "WARNING: this batch used a scripted fake model" in text and "eval-test" in text
    section = text.split("## Audit enforced vs advisory")[1].split("## Every run")[0]
    assert "| enforce (audit result fed back, repairs allowed) | 1 | 1/1 (100%) | 0/1 (0%) | 1/1 (100%) |" in section
    assert "| advisory (audit recorded only; the first claim is final) | 1 | 1/1 (100%) | 1/1 (100%) | 0/1 (0%) |" in section


def test_runs_do_not_leak_into_each_other():
    """Two repeats: the second must start from the seed, not from the first run's bill."""
    from tests.agent.test_loop import t1_script  # noqa: F811
    table = {"S1": prep("vendor_invoice_entry") + t1_script()}
    import tempfile
    with tempfile.TemporaryDirectory() as runs:
        _, records = harness.run_batch(select(ALL, ["S1"]), repeats=2, repo_root=ROOT, runs_dir=Path(runs),
                                       llm_factory=lambda s, r: FakeLLM(prep("vendor_invoice_entry") + t1_script()),
                                       use_browser=False)
    assert [r["outcome"] for r in records] == ["verified_done", "verified_done"] and all(r["ground_truth_ok"] for r in records)
    assert [r["repeat"] for r in records] == [1, 2] and table


def test_an_auditor_false_pass_is_exposed_by_the_independent_ground_truth(tmp_path):
    """The agent enters an OLDER invoice and writes assertions that agree with itself. The audit passes (it checks what it
    is told to check); the ground truth, written from the seed, says the world is wrong. This is the documented limit of
    agent-written assertions, and the harness reports it."""
    def field(name):
        return {"id": name, "kind": "field_equals",
                "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": "INV-2038"}, "field": name},
                "expected": {"from": "query", "db": "portal", "table": "portal_invoices", "where": {"invoice_no": "INV-2038"}, "field": name}}
    weak = yaml.safe_dump({"assertions": [*[field(f) for f in ("vendor_id", "invoice_no", "amount", "due_date")],
                                          {"kind": "rows_added", "table": "erp_bills", "count": 1},
                                          {"kind": "no_other_changes", "except_tables": ["erp_bills"]}]})
    wrong = dict(method="POST", url="/erp/api/bills", json={"vendor_id": 7, "invoice_no": "INV-2038", "amount": 29990, "due_date": "2026-09-03"})
    script = prep("vendor_invoice_entry") + [call("http_request", **wrong), call("declare_assertions", yaml=weak),
                                             call("finish", status="done", summary="Entered the invoice.")]
    s1 = select(ALL, ["S1"])[0]
    scenario = Scenario.model_validate({**s1.model_dump(), "id": "S1-wrong-invoice"})
    _, records = harness.run_batch([scenario], repeats=1, repo_root=ROOT, runs_dir=tmp_path,
                                   llm_factory=lambda s, r: FakeLLM(script), use_browser=False)
    r = records[0]
    assert r["outcome"] == "verified_done" and r["verified_success"] and not r["ground_truth_ok"]
    assert r["auditor_false_pass"] and not r["meets_expectations"]
    assert "auditor false pass" in render_results({**harness_batch(), "scenarios": ["S1-wrong-invoice"]}, records).lower()


def harness_batch():
    return {"batch_id": "b", "started_at": "t0", "finished_at": "t1", "model": "fake/scripted", "temperature": None,
            "repeats": 1, "scenarios": ["S1"], "command": "x", "limits": "none"}


def test_a_crash_in_one_run_is_recorded_and_the_batch_carries_on(tmp_path):
    def flaky(scenario, repeat):
        if repeat == 1:
            raise RuntimeError("model client could not be created")
        return FakeLLM(prep("vendor_invoice_entry") + t1_script())

    _, records = harness.run_batch(select(ALL, ["S1"]), repeats=2, repo_root=ROOT, runs_dir=tmp_path, llm_factory=flaky, use_browser=False)
    assert [r["outcome"] for r in records] == ["harness_error", "verified_done"]
    assert records[0]["unmet"] == ["RuntimeError: model client could not be created"] and not records[0]["meets_expectations"]


def test_the_spend_limit_stops_new_runs_and_says_which_were_skipped(tmp_path):
    batch, records = harness.run_batch(select(ALL, ["S1"]), repeats=3, repo_root=ROOT, runs_dir=tmp_path, use_browser=False,
                                       llm_factory=lambda s, r: FakeLLM(prep("vendor_invoice_entry") + t1_script()), budget_usd=0.005)
    assert [r["outcome"] for r in records] == ["verified_done", "skipped_budget", "skipped_budget"]
    assert "$0.005 for the whole batch" in batch["limits"]
    text = render_results(batch, records)
    assert "2 run(s) were skipped because the spend limit was reached" in text and "S1#2, S1#3" in text


def test_the_command_line_regenerates_results_from_a_stored_batch(batch_result, tmp_path, capsys):
    batch, records, _ = batch_result
    stored = tmp_path / "batch.json"
    stored.write_text(json.dumps({"batch": batch, "records": list(records.values())}), encoding="utf-8")
    out = tmp_path / "RESULTS.md"
    assert harness.main(["--results-from", str(stored), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == render_results(batch, list(records.values()))
    assert "Wrote" in capsys.readouterr().out


def test_the_command_line_refuses_to_start_without_an_api_key(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert harness.main(["--repeats", "1"]) == 1
    out = capsys.readouterr().out
    assert "GEMINI_API_KEY is not set" in out and "gemini/gemini-3.8-flash" in out and "No runs were started" in out


def test_the_command_line_checks_the_key_of_whichever_provider_is_configured(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MODEL", "openai/gpt-x")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert harness.main(["--repeats", "1"]) == 1
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY is not set" in out and "No runs were started" in out
