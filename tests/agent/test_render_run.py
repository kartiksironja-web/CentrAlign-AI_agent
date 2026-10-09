"""The run viewer: a static HTML timeline of run.json, built from real scripted runs."""
import json
import re

from agent.human import ScriptedHuman
from scripts.render_run import main, render_html
from tests.agent.fakes import call, prep
from tests.agent.test_loop import BIG, GOOD_BILL, assertions_yaml, date_trap_claim, run_loop, t1_script  # noqa: F401


def read_log(tmp_path, result):
    return json.loads((tmp_path / "runs" / result.run_id / "run.json").read_text(encoding="utf-8"))


def test_a_repaired_false_claim_shows_the_reasons_the_rejection_and_the_audit_table(run_loop, tmp_path):
    script = prep("vendor_invoice_entry") + date_trap_claim() + [
        call("http_request", method="PUT", url="/erp/api/bills/3", json={"due_date": "2026-11-03"}, why="Fix the due date the audit flagged"),
        call("finish", status="done", summary="Fixed it."),
    ]
    result, _, _ = run_loop(script, faults=["DATE_TRAP"])
    page = render_html(read_log(tmp_path, result))
    assert f"Run {result.run_id}" in page and 'class="badge good">verified_done' in page
    assert "Why: Fix the due date the audit flagged" in page and "FINISH REJECTED: the independent audit failed." in page
    assert page.count("Independent audit attempts") == 1 and "FAILED" in page and "PASS" in page and "FAIL<" in page
    assert "shown to the agent" in page and "Declared at step" in page and "Plan version 1" in page
    assert "<th>expected</th>" in page and "2026-11-03" in page and "2026-03-11" in page
    assert "OUTCOME: DONE and VERIFIED" in page and "Changes observed in the company systems" in page
    assert "http_request" in page and "create_bill" in page and "(create_bill)" in page


def test_gate_blocks_approvals_and_scripted_humans_are_visible(run_loop, tmp_path):
    script = prep("vendor_invoice_entry") + [
        call("http_request", **BIG), call("ask_human", kind="approval", question="Please approve this bill"),
        call("finish", status="needs_human", summary="stopping here for the demo"),
    ]
    result, _, _ = run_loop(script, human=ScriptedHuman({"approval": ["deny"]}))
    page = render_html(read_log(tmp_path, result))
    assert "approval required by bill_over_limit" in page and "approval needed" in page
    assert 'class="badge warn">SCRIPTED' in page and "Please approve this bill" in page and "Reply: <code>deny</code>" in page
    assert "What the system would actually do: create_bill: vendor_id=5" in page
    assert "needs_human" in page


def test_multi_line_arguments_such_as_declared_assertions_are_shown_as_readable_blocks(run_loop, tmp_path):
    result, _, _ = run_loop(prep("vendor_invoice_entry") + t1_script())
    page = render_html(read_log(tmp_path, result))
    declare = page.split("<code>declare_assertions</code>")[1].split("</section>")[0]
    assert "<p>yaml:</p><pre>assertions:\n- actual:" in declare  # real line breaks, not \n escapes
    assert "\\n" not in declare.split("<details>")[0]


def test_reads_are_summarised_not_listed_one_by_one(run_loop, tmp_path):
    result, _, _ = run_loop(prep("vendor_invoice_entry") + t1_script())
    page = render_html(read_log(tmp_path, result))
    assert "read request(s) allowed" in page
    assert "POST http://127.0.0.1:" in page and "/erp/api/bills" in page  # the one write is listed in full


def test_everything_from_the_run_is_escaped_and_no_script_can_run(run_loop, tmp_path):
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    script = prep() + [call("note", key=evil, value=evil), call("finish", status="blocked", summary=evil)]
    result, _, _ = run_loop(script)
    log = read_log(tmp_path, result)
    log["request"] = evil
    page = render_html(log)
    assert "&lt;script&gt;" in page and "&lt;img src=x onerror=" in page
    assert not re.search(r"<(script|img|iframe|a|link)\b", page, re.I)  # no tag the viewer did not write itself
    assert "default-src 'none'" in page  # and a content security policy forbids scripts anyway
    assert "onerror" not in re.sub(r"&lt;.*?&gt;", "", page.replace("&quot;", '"'), flags=re.S).split("<main>")[0]


def test_partial_or_failed_logs_do_not_crash_the_viewer():
    assert "Run x" in render_html({"run_id": "x"})
    broken = {"run_id": "e1", "request": "r", "outcome": {"outcome": "error", "detail": "AuthenticationError while calling a model"},
              "steps": [], "metrics": {"steps": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "wall_seconds": 5.2}}
    page = render_html(broken)
    assert 'class="badge bad">error' in page and "AuthenticationError while calling a model" in page and "no steps" in page


def test_the_command_line_writes_the_page_next_to_the_log_or_where_told(run_loop, tmp_path, capsys):
    result, _, _ = run_loop(prep("vendor_invoice_entry") + t1_script())
    log_path = tmp_path / "runs" / result.run_id / "run.json"
    assert main([str(log_path)]) == 0
    default = log_path.with_name("timeline.html")
    assert default.exists() and f"Run {result.run_id}" in default.read_text(encoding="utf-8")
    elsewhere = tmp_path / "out.html"
    assert main([str(log_path), "--out", str(elsewhere)]) == 0 and elsewhere.read_text(encoding="utf-8") == default.read_text(encoding="utf-8")
    assert "Wrote" in capsys.readouterr().out
