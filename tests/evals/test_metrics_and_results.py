"""Per-run metrics and the RESULTS.md renderer, with exact numbers on hand-built records."""
from pathlib import Path

import pytest

from evals.ground_truth import GroundResult
from evals.metrics import derive_record, failed_record, first_write_and_human
from evals.results import mean, money, ratio, render_results
from evals.scenarios import load_scenarios

SCEN = {s.id: s for s in load_scenarios(Path(__file__).resolve().parents[2] / "evals" / "scenarios.yaml")}
ZERO = {"tool_error": 0, "timeout": 0, "noop": 0, "unexpected_page": 0, "repeat": 0}
OK = GroundResult(True, [])
CLAIM_OK = {"step": 5, "status": "done", "audit_passed": True}
CLAIM_BAD = {"step": 5, "status": "done", "audit_passed": False}


def run(outcome="verified_done", claim=CLAIM_OK, self_checks=(), attempts=(), steps=(), interactions=(), detectors=None,
        gate_blocks=0, n_steps=6, cost=0.2):
    return {"run_id": "r1", "outcome": {"outcome": outcome}, "config": {"model": "fake/scripted", "temperature": None},
            "metrics": {"steps": n_steps, "detector_counts": {**ZERO, **(detectors or {})}, "gate_blocks": gate_blocks,
                        "first_claim": claim, "tokens_in": 1000, "tokens_out": 100, "cost_usd": cost, "wall_seconds": 12.0,
                        "assertion_versions": 1},
            "audit_attempts": list(attempts), "self_checks": list(self_checks), "steps": list(steps), "interactions": list(interactions)}


def step(n, tool="http_request", result="", gate=()):
    return {"n": n, "tool": tool, "result_text": result, "gate": list(gate)}


def write_entry(method="POST", effect="allow", note=""):
    return {"method": method, "effect": effect, "note": note}


META = {"batch": "b", "repeat": 1}


def record(scenario="S1", **kwargs):
    ground = kwargs.pop("ground", OK)
    return derive_record(run(**kwargs), SCEN[scenario], ground, META)


# ---- derive_record ------------------------------------------------------------------

def test_a_clean_verified_run_meets_expectations():
    r = record()
    assert r["meets_expectations"] and r["unmet"] == [] and r["verified_success"] and r["ground_truth_ok"]
    assert (r["first_claim_correct"], r["false_completion"], r["self_caught"], r["auditor_caught"], r["auditor_false_pass"]) == (True, False, False, False, False)
    assert (r["steps"], r["retries"], r["humans"], r["cost_usd"]) == (6, 0, 0, 0.2) and r["run_id"] == "r1" and r["batch"] == "b"


def test_a_false_first_claim_that_the_audit_then_caught_and_the_agent_repaired():
    r = record(claim=CLAIM_BAD, attempts=[{"shown_to_agent": True, "passed": False}, {"shown_to_agent": True, "passed": True}])
    assert r["false_completion"] and r["first_claim_correct"] is False and r["auditor_caught"] and r["meets_expectations"]
    assert r["retries"] == 1  # one audit failure shown to the agent


def test_a_repair_the_agent_made_itself_is_a_self_catch_not_an_audit_catch():
    r = record(self_checks=[{"passed": False}, {"passed": True}])
    assert r["self_caught"] and not r["auditor_caught"] and not r["false_completion"]


def test_advisory_audit_failures_are_not_shown_so_they_are_not_audit_catches():
    r = record("S3-advisory", outcome="false_completion", claim=CLAIM_BAD, attempts=[{"shown_to_agent": False, "passed": False}],
              ground=GroundResult(False, ["erp_bills where {...}: found 0 row(s), expected 1"]))
    assert r["false_completion"] and not r["auditor_caught"] and not r["verified_success"] and not r["auditor_false_pass"]
    assert not r["meets_expectations"] and r["unmet"][0].startswith("outcome 'false_completion' is not one of")
    assert r["unmet"][1].startswith("ground truth: erp_bills")


def test_an_auditor_false_pass_is_flagged_when_the_audit_passed_but_the_world_is_wrong():
    r = record(ground=GroundResult(False, ["erp_bills: +1 rows, expected +0"]))
    assert r["verified_success"] and r["auditor_false_pass"] and not r["meets_expectations"]


def test_a_run_that_never_claimed_completion_has_no_first_claim_rates():
    r = record("S5-deny", outcome="needs_human", claim=None, gate_blocks=1,
               interactions=[{"scripted": True}])
    assert r["first_claim_made"] is False and r["first_claim_correct"] is None and r["false_completion"] is False
    assert r["meets_expectations"] and r["humans"] == 1 and r["humans_scripted"] == 1


@pytest.mark.parametrize("scenario, kwargs, fragment", [
    ("S1", {"interactions": [{"scripted": True}]}, "1 human interaction(s), expected 0..0"),
    ("S2", {}, "0 human interaction(s), expected 1..any"),
    ("S5-approve", {"interactions": [{"scripted": True}]}, "0 gate block(s), expected at least 1"),
    ("S1", {"outcome": "audit_failed"}, "outcome 'audit_failed' is not one of ['verified_done']"),
])
def test_each_expectation_can_fail_on_its_own(scenario, kwargs, fragment):
    r = record(scenario, **kwargs)
    assert not r["meets_expectations"] and any(fragment in u for u in r["unmet"])


def test_retries_add_detector_firings_and_shown_audit_failures():
    r = record(detectors={"noop": 2, "tool_error": 1}, attempts=[{"shown_to_agent": True, "passed": False}])
    assert r["retries"] == 4 and r["detectors"]["noop"] == 2


def test_write_before_the_first_human_answer_is_found_in_the_steps():
    steps = [step(1, gate=[write_entry("GET")]), step(2, gate=[write_entry("POST")]),
             step(3, "ask_human", "HUMAN REPLY: Pune")]
    assert first_write_and_human(steps) == (2, 3)
    r = record("S2", steps=steps, interactions=[{"scripted": True}])
    assert any("a write reached the systems at step 2 before the first human answer (3)" in u for u in r["unmet"])
    good = [step(1, gate=[write_entry("GET")]), step(2, "ask_human", "HUMAN REPLY: Pune"), step(3, gate=[write_entry("POST")])]
    assert record("S2", steps=good, interactions=[{"scripted": True}])["meets_expectations"]


def test_blocked_writes_and_redirect_checks_do_not_count_as_writes_that_reached_the_systems():
    steps = [step(1, gate=[write_entry("POST", effect="require_approval")]), step(2, gate=[write_entry("POST", note="redirect hop 1")]),
             step(3, "ask_human", "APPROVED by the human: x"), step(4, gate=[write_entry("POST")])]
    assert first_write_and_human(steps) == (4, 3)


def test_a_failed_record_counts_as_a_failure_with_the_reason():
    r = failed_record(SCEN["S1"], META, "harness_error", "RuntimeError: boom")
    assert r["outcome"] == "harness_error" and not r["meets_expectations"] and r["unmet"] == ["RuntimeError: boom"]
    assert r["run_id"] is None and r["scenario"] == "S1" and r["repeat"] == 1


# ---- formatting helpers ----------------------------------------------------------------

def test_ratio_mean_and_money():
    assert ratio(2, 3) == "2/3 (67%)" and ratio(0, 0) == "n/a" and ratio(3, 3) == "3/3 (100%)"
    assert mean([1, 2, 4]) == "2.3" and mean([]) == "n/a" and mean([1, None]) == "unknown" and mean([10, 20], 0) == "15"
    assert money([0.1, 0.3], "avg") == "$0.200" and money([0.1, 0.3], "sum") == "$0.40"
    assert money([0.1, None], "avg") == "unknown" and money([], "avg") == "n/a"


# ---- the renderer ------------------------------------------------------------------------

def batch(**kwargs):
    base = {"batch_id": "eval-x", "started_at": "2026-10-09T10:00:00", "finished_at": "2026-10-09T10:30:00",
            "model": "gemini/gemini-3.8-flash", "temperature": 0.0, "repeats": 3, "scenarios": ["S1", "S3", "S3-advisory"],
            "command": "python -m evals.run_evals --repeats 3", "limits": "40 steps, 300 s and $1.0 per run"}
    return {**base, **kwargs}


def rec(sid, n, **kwargs):
    r = record(sid, **kwargs)
    return {**r, "scenario": sid, "repeat": n, "run_id": f"{sid}-{n}"}


def sample_records():
    return [
        rec("S1", 1), rec("S1", 2), rec("S1", 3, outcome="budget_exhausted", claim=None, ground=GroundResult(False, ["erp_bills: +0 rows, expected +1"])),
        rec("S3", 1, claim=CLAIM_BAD, attempts=[{"shown_to_agent": True, "passed": False}]), rec("S3", 2), rec("S3", 3, self_checks=[{"passed": False}]),
        rec("S3-advisory", 1, outcome="false_completion", claim=CLAIM_BAD, ground=GroundResult(False, ["x"])),
        rec("S3-advisory", 2, outcome="false_completion", claim=CLAIM_BAD, ground=GroundResult(False, ["x"])),
        rec("S3-advisory", 3),
    ]


def test_the_summary_table_counts_what_the_records_say():
    text = render_results(batch(), sample_records())
    s1 = next(l for l in text.splitlines() if l.startswith("| S1 |") and "Meets" not in l)
    assert s1.split(" | ")[1:6] == ["3", "2/3 (67%)", "2/3 (67%)", "2/3 (67%)", "2/2 (100%)"]  # runs, meets, verified, truth, first claim (claims only)
    s3 = next(l for l in text.splitlines() if l.startswith("| S3 |"))
    cells = [c.strip() for c in s3.strip("|").split("|")]
    assert cells[1:] == ["3", "3/3 (100%)", "3/3 (100%)", "3/3 (100%)", "2/3 (67%)", "1/3 (33%)", "1/3 (33%)", "1/3 (33%)", "0/3 (0%)"]


def test_enforce_versus_advisory_shows_how_many_wrong_results_stay_standing():
    text = render_results(batch(), sample_records())
    section = text.split("## Audit enforced vs advisory")[1].split("## Every run")[0]
    enforce = next(l for l in section.splitlines() if l.startswith("| enforce"))
    advisory = next(l for l in section.splitlines() if l.startswith("| advisory"))
    assert enforce.endswith("| 3 | 1/3 (33%) | 0/3 (0%) | 3/3 (100%) |")
    assert advisory.endswith("| 3 | 2/3 (67%) | 2/3 (67%) | 1/3 (33%) |")


def test_provenance_and_the_do_not_edit_notice_are_always_present():
    text = render_results(batch(), sample_records())
    assert text.startswith("# Evaluation results") and "**Do not edit by hand.**" in text
    assert "- model: `gemini/gemini-3.8-flash`, temperature: 0.0" in text and "SCRIPTED_HUMAN" in text
    assert "one run moves a rate by 33 percentage points" in text and "WARNING" not in text


def test_a_fake_model_batch_is_loudly_labelled():
    text = render_results(batch(model="fake/scripted", temperature=None), sample_records())
    assert "WARNING: this batch used a scripted fake model" in text and "temperature: not sent" in text


def test_skipped_and_failed_runs_are_listed_not_hidden():
    records = sample_records() + [failed_record(SCEN["S1"], {"batch": "b", "repeat": 4}, "skipped_budget", "spend limit reached"),
                                  failed_record(SCEN["S1"], {"batch": "b", "repeat": 5}, "harness_error", "RuntimeError: boom")]
    text = render_results(batch(), records)
    assert "## Incomplete runs" in text and "1 run(s) were skipped because the spend limit was reached" in text and "S1#4" in text
    assert "S1#5: harness_error: RuntimeError: boom" in text
    s1 = next(l for l in text.splitlines() if l.startswith("| S1 |") and "Meets" not in l)
    assert s1.split(" | ")[1] == "4"  # skipped runs are not counted; the harness error is (as a failure)


def test_unknown_cost_is_reported_as_unknown_not_guessed():
    records = [rec("S1", 1), {**rec("S1", 2), "cost_usd": None}]
    text = render_results(batch(scenarios=["S1"]), records)
    assert "cost unknown" in text.split("Total:")[1].split("\n")[0] and "| unknown |" in text


def test_every_run_appears_in_the_run_list_with_its_unmet_reasons():
    text = render_results(batch(), sample_records())
    row = next(l for l in text.split("## Every run")[1].splitlines() if l.startswith("| S1 | 3 |"))
    assert "budget_exhausted" in row and "WRONG" in row and "| NO |" in row and "ground truth: erp_bills: +0 rows" in row
    run_list = text.split("## Every run")[1]
    assert sum(1 for l in run_list.splitlines() if l.startswith("| S3-advisory |")) == 3
