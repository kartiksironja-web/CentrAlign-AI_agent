"""The runner-side helpers and the command line."""
import json
from urllib.parse import urlsplit

import httpx
import pytest

import agent.run as cli
from agent.runner import AuditorAdapter, apply_faults
from tests.agent.live import free_port


@pytest.fixture(autouse=True)
def local_model(monkeypatch):
    """These tests must not depend on the developer's .env: a model with no key requirement."""
    monkeypatch.setenv("AGENT_MODEL", "local/test-model")


def test_the_cli_refuses_to_start_without_the_key_for_the_chosen_provider(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MODEL", "gemini/gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert cli.main(["--task", "anything"]) == 1
    out = capsys.readouterr().out
    assert "GEMINI_API_KEY is not set" in out and "Nothing was started" in out


def faults_now(comp):
    return httpx.get(comp.url + "/admin/faults", headers={"X-Admin-Token": "t"}).json()["active"]


def test_apply_faults_sets_flags_and_can_reset_the_world(company):
    comp = company()
    apply_faults(comp.url, "t", ["DATE_TRAP", "SAVE_BROKEN"])
    assert faults_now(comp) == ["DATE_TRAP", "SAVE_BROKEN"]
    httpx.post(comp.url + "/erp/api/bills", json={"vendor_id": 7, "invoice_no": "X", "amount": 5, "due_date": "2026-11-03"})
    apply_faults(comp.url, "t", [], reset=True)
    assert faults_now(comp) == [] and len(comp.query("SELECT * FROM erp_bills")) == 2


def test_apply_faults_fails_loudly_with_a_wrong_token_or_unknown_fault(company):
    comp = company()
    with pytest.raises(RuntimeError, match="HTTP 403"):
        apply_faults(comp.url, "wrong", ["DATE_TRAP"])
    with pytest.raises(RuntimeError, match="HTTP 422"):
        apply_faults(comp.url, "t", ["NOPE"])


def test_the_cli_says_so_when_the_app_is_not_running(monkeypatch, capsys):
    monkeypatch.setenv("PORT", str(free_port()))
    assert cli.main(["--task", "anything"]) == 1
    assert "Nothing is answering at http://127.0.0.1:" in capsys.readouterr().out


def test_the_cli_passes_its_flags_to_the_runner_and_prepares_the_world(company, monkeypatch, capsys):
    comp = company()
    monkeypatch.setenv("PORT", str(urlsplit(comp.url).port))
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    monkeypatch.setenv("COMPANY_DATA_DIR", str(comp.data_dir))
    seen = {}

    class Result:
        outcome, report = "verified_done", "THE REPORT ₹"

    def fake_run_task(task, settings):
        seen["task"], seen["settings"] = task, settings
        return Result()

    monkeypatch.setattr(cli, "run_task", fake_run_task)
    code = cli.main(["--task", "do it", "--headed", "--slow", "250", "--faults", "DATE_TRAP, SAVE_BROKEN", "--reset",
                     "--audit-mode", "advisory", "--max-steps", "7", "--max-seconds", "60", "--max-cost", "0.5"])
    s = seen["settings"]
    assert code == 0 and seen["task"] == "do it" and "THE REPORT" in capsys.readouterr().out
    assert (s.headless, s.slow_mo_ms, s.base_url) == (False, 250, comp.url) and s.human.scripted is False
    assert (s.config.audit_mode, s.config.max_steps, s.config.max_seconds, s.config.max_cost_usd) == ("advisory", 7, 60.0, 0.5)
    assert s.run_info == {"faults": ["DATE_TRAP", "SAVE_BROKEN"]} and faults_now(comp) == ["DATE_TRAP", "SAVE_BROKEN"]


def test_exit_codes_reflect_the_outcome(company, monkeypatch):
    comp = company()
    monkeypatch.setenv("PORT", str(urlsplit(comp.url).port))
    for outcome, code in (("verified_done", 0), ("verified_no_action", 0), ("needs_human", 2), ("blocked", 2),
                          ("audit_failed", 1), ("budget_exhausted", 1), ("error", 1), ("false_completion", 1)):
        monkeypatch.setattr(cli, "run_task", lambda task, s, o=outcome: type("R", (), {"outcome": o, "report": "r"})())
        assert cli.main(["--task", "x"]) == code, outcome


def test_a_declaration_whose_expected_value_cannot_be_obtained_is_rejected_at_once(company, tmp_path):
    """Seen in real runs: a wrong pattern or lookup was accepted and only failed the audit later."""
    from auditor.config import load_config
    from tests.agent.helpers import PACK

    comp = company()
    adapter = AuditorAdapter(load_config(PACK / "audit.yaml", {"data": comp.data_dir, "repo": tmp_path}))
    try:
        def declare(vendor_name):
            return adapter.declare(
                "assertions: [{id: v, kind: field_equals, actual: {db: erp, table: erp_vendors, where: {id: 7}, field: id},"
                " expected: {from: query, db: erp, table: erp_vendors, where: {name: '" + vendor_name + "'}, field: id}}]", {}, {})
        missing = declare("Nobody Ltd")
        assert not missing.ok and "assertion 'v': expected value unavailable: the expected-value query matched no rows" in missing.message
        assert declare("Acme Supplies Pvt Ltd").ok
    finally:
        adapter.close()


def test_the_auditor_adapter_accepts_and_rejects_declarations_with_readable_messages(company, tmp_path):
    from auditor.config import load_config
    from tests.agent.helpers import PACK

    comp = company()
    adapter = AuditorAdapter(load_config(PACK / "audit.yaml", {"data": comp.data_dir, "repo": tmp_path}))
    try:
        bad = adapter.declare("assertions: [{kind: wibble}]", {}, {})
        assert not bad.ok and bad.message.startswith("ASSERTIONS NOT ACCEPTED (format):") and "'kind' must be one of" in bad.message
        unknown = adapter.declare("assertions: [{kind: rows_added, table: nope, count: 1}]", {}, {})
        assert not unknown.ok and "unknown table 'nope'" in unknown.message
        good = adapter.declare("assertions: [{id: a, kind: rows_added, table: erp_bills, count: 0}]", {}, {})
        assert good.ok and "Assertions accepted (1 checks: a)" in good.message
        view = adapter.audit("assertions: [{id: a, kind: rows_added, table: erp_bills, count: 0}]", {}, {}, 1)
        assert view.valid and view.passed and adapter.changes() == {}
        httpx.post(comp.url + "/erp/api/bills", json={"vendor_id": 7, "invoice_no": "X", "amount": 5, "due_date": "2026-11-03"})
        assert list(adapter.changes()) == ["erp.erp_bills"]
        failed = adapter.audit("assertions: [{id: a, kind: rows_added, table: erp_bills, count: 0}, {kind: no_other_changes}]", {}, {}, 1)
        assert not failed.passed and "AUDIT FAILED (feedback round 1)" in failed.feedback and json.dumps(failed.detail)
    finally:
        adapter.close()
