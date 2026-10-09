"""The web console: the human who answers in the browser, the live reading of the run log, the API and a whole run through it.

Lives in tests/agent so it can use the same live company app and scripted-model helpers as the agent tests.
"""
import json
import os
import threading
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from agent.human import NoHumanReply
from agent.runlog import RunLog
from apps.console.markdown import to_html
from apps.console.runs import WebHuman, install_log_lock, read_run_json
from apps.console.server import create_app
from tests.agent.fakes import FakeLLM, call, prep


# ---- the human at the keyboard of a web page ----------------------------------------------------

def test_the_agent_waits_until_the_page_answers():
    human, got = WebHuman(wait_seconds=5), []
    thread = threading.Thread(target=lambda: got.append(human.ask("approval", "may I?", ["approve", "deny"], ["what the system would do"])))
    thread.start()
    for _ in range(50):
        if human.pending:
            break
        time.sleep(0.05)
    assert human.pending["kind"] == "approval" and human.pending["context"] == ["what the system would do"]
    assert human.answer(human.pending["id"] + 99, "approve") is False  # an answer to some other question is refused
    assert human.answer(human.pending["id"], "approve") is True
    thread.join(5)
    assert got == ["approve"] and human.pending is None and human.scripted is False


def test_no_answer_means_no_human_available():
    with pytest.raises(NoHumanReply):
        WebHuman(wait_seconds=0.05).ask("clarification", "which one?", None, [])


def test_closing_releases_a_waiting_agent():
    human = WebHuman(wait_seconds=30)
    threading.Timer(0.1, human.close).start()
    with pytest.raises(NoHumanReply):
        human.ask("clarification", "which one?", None, [])


# ---- reading a log that is being replaced -----------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="the lock only exists on Windows")
def test_any_open_handle_blocks_the_agents_replace_on_windows():
    """Documents WHY the console takes turns with the agent: on Windows os.replace fails while ANY handle to the file is open."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        log, new = Path(d) / "run.json", Path(d) / "run.tmp"
        log.write_text('{"a": 1}')
        new.write_text('{"a": 2}')
        with open(log, encoding="utf-8"):
            with pytest.raises(PermissionError):
                os.replace(new, log)


def test_the_agents_log_writes_and_the_consoles_reads_never_overlap(tmp_path):
    """Seen once in the evaluation: 'Access is denied' replacing run.json while another process was reading it."""
    install_log_lock()
    log = RunLog(tmp_path)
    log.set("steps", [{"n": i, "pad": "x" * 2000} for i in range(60)])
    failures, stop = [], threading.Event()

    def writer():
        try:
            for _ in range(400):
                log.write()
        except Exception as e:  # PermissionError on Windows when a read overlaps the replace
            failures.append(repr(e))
        finally:
            stop.set()

    thread = threading.Thread(target=writer)
    thread.start()
    reads = 0
    while not stop.is_set():
        read_run_json(tmp_path)
        reads += 1
    thread.join()
    assert failures == [] and reads >= 1


def test_the_live_log_is_none_before_the_first_write_and_the_dict_after(tmp_path):
    assert read_run_json(tmp_path) is None
    (tmp_path / "20260101-000000-abcd").mkdir()
    (tmp_path / "20260101-000000-abcd" / "run.json").write_text(json.dumps({"steps": [1, 2]}))
    assert read_run_json(tmp_path) == {"steps": [1, 2]}
    (tmp_path / "20260101-000000-abcd" / "run.json").write_text("{half a file")
    assert read_run_json(tmp_path) is None  # a torn read is skipped, the next poll will see the whole file


# ---- reading the company's tables while the runner resets them ----------------------------------------

def test_reading_the_tables_survives_a_reset_in_progress(tmp_path, monkeypatch):
    """Found in a fresh-clone run: a read between DROP TABLE and CREATE TABLE answered the page with HTTP 500."""
    import sqlite3

    from apps.console import runs

    (tmp_path / "company.db").write_bytes(b"")  # a database with no tables yet, as during a reset
    started = time.time()
    assert runs.read_tables(tmp_path) == {"vendors": [], "bills": [], "invoices": []}
    assert time.time() - started < 5

    con = sqlite3.connect(tmp_path / "company.db")
    con.executescript("""CREATE TABLE erp_vendors (id INTEGER, name TEXT, city TEXT, payment_terms_days INTEGER);
        CREATE TABLE erp_bills (id INTEGER, vendor_id INTEGER, invoice_no TEXT, amount REAL, due_date TEXT, status TEXT, created_via TEXT);
        CREATE TABLE portal_invoices (invoice_no TEXT, vendor_id INTEGER, invoice_date TEXT, amount REAL, due_date TEXT, revised_of TEXT);
        INSERT INTO erp_vendors VALUES (1, 'V', 'C', 30);""")
    con.commit()
    con.close()
    assert runs.read_tables(tmp_path)["vendors"] == [{"id": 1, "name": "V", "city": "C", "payment_terms_days": 30}]


def test_a_read_that_fails_once_is_retried(tmp_path, monkeypatch):
    from apps.console import runs

    calls = []

    def flaky(data_dir):
        calls.append(1)
        if len(calls) < 3:
            raise runs.sqlite3.OperationalError("no such table: erp_vendors")
        return {"vendors": [1], "bills": [], "invoices": []}

    monkeypatch.setattr(runs, "_read_tables_once", flaky)
    assert runs.read_tables(tmp_path)["vendors"] == [1] and len(calls) == 3


# ---- the markdown of the results files ----------------------------------------------------------------

def test_markdown_tables_headings_and_escaping():
    html = to_html("## Title\n\n| a | b |\n|---|---|\n| 1 | <script>x</script> |\n\n- one\n- **two** `code`\n")
    assert "<h3>Title</h3>" in html and "<th>a</th>" in html and "<td>1</td>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html and "<li>one</li>" in html and "<b>two</b>" in html and "<code>code</code>" in html


# ---- the API ---------------------------------------------------------------------------------------------

@pytest.fixture
def console(company, tmp_path):
    comp = company()
    holder = {}

    def hook(settings):  # a scripted model and no real browser, so the test needs neither a key nor Chromium
        settings.llm = holder["llm"]
        settings.use_browser = False

    app = create_app(base_url=comp.url, data_dir=comp.data_dir, admin_token="t", settings_hook=hook, runs_root=tmp_path / "console_runs")
    client = TestClient(app)
    client.holder = holder
    client.comp = comp
    return client


def wait_for(client, handle, status=("finished", "error"), timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/runs/{handle}").json()
        if state["status"] in status:
            return state
        time.sleep(0.2)
    raise AssertionError("the run did not finish in time")


def test_config_lists_the_evaluation_scenarios_as_presets(console):
    cfg = console.get("/api/config").json()
    ids = [p["id"] for p in cfg["presets"]]
    assert ids[:3] == ["S1", "S2", "S3"] and "S5-approve" in ids and cfg["company_up"] is True
    assert {"DATE_TRAP", "SAVE_BROKEN", "BUTTON_RENAME", "DUPLICATE_VENDOR"} == set(cfg["faults"])
    assert next(p for p in cfg["presets"] if p["id"] == "S5-approve")["human"] == "approval"


def test_bad_requests_are_refused_with_a_reason(console):
    assert console.post("/api/runs", json={"task": "x"}).status_code == 422                   # too short
    assert console.post("/api/runs", json={"task": "do it please", "faults": ["NOPE"]}).status_code == 422
    assert console.post("/api/runs", json={"task": "do it please", "audit_mode": "maybe"}).status_code == 422
    assert console.get("/api/runs/unknown").status_code == 404
    assert console.get("/api/runs/unknown/timeline").status_code == 404


def test_a_whole_run_through_the_console_shows_steps_gate_audit_and_changes(console):
    GOOD = dict(method="POST", url="/erp/api/bills", json={"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200, "due_date": "2026-11-03"})
    yaml_text = yaml.safe_dump({"assertions": [
        {"kind": "field_equals", "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": "INV-2041"}, "field": "vendor_id"},
         "expected": {"from": "query", "db": "erp", "table": "erp_vendors", "where": {"name": "Acme Supplies Pvt Ltd"}, "field": "id"}},
        {"kind": "field_equals", "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": "INV-2041"}, "field": "invoice_no"},
         "expected": {"from": "query", "db": "portal", "table": "portal_invoices", "where": {"invoice_no": "INV-2041"}, "field": "invoice_no"}},
        {"kind": "field_equals", "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": "INV-2041"}, "field": "amount"},
         "expected": {"from": "query", "db": "portal", "table": "portal_invoices", "where": {"invoice_no": "INV-2041"}, "field": "amount"}},
        {"kind": "field_equals", "actual": {"db": "erp", "table": "erp_bills", "where": {"invoice_no": "INV-2041"}, "field": "due_date"},
         "expected": {"from": "query", "db": "portal", "table": "portal_invoices", "where": {"invoice_no": "INV-2041"}, "field": "due_date"}},
        {"kind": "rows_added", "table": "erp_bills", "count": 1},
        {"kind": "no_other_changes", "except_tables": ["erp_bills"]},
    ]})
    console.holder["llm"] = FakeLLM(prep("vendor_invoice_entry") + [
        call("http_request", **GOOD),
        call("self_check", passed=True, detail="read the bill back"),
        call("declare_assertions", yaml=yaml_text),
        call("finish", status="done", summary="Entered INV-2041."),
    ])
    started = console.post("/api/runs", json={"task": "Enter Acme Supplies' latest invoice", "reset": True, "headed": False})
    assert started.status_code == 200
    handle = started.json()["handle"]
    assert console.post("/api/runs", json={"task": "a second run at once"}).status_code in (409, 200)  # one at a time (409 unless it already ended)
    state = wait_for(console, handle)
    assert state["status"] == "finished" and state["result"]["outcome"] == "verified_done", state.get("error")
    run = state["run"]
    assert [s["tool"] for s in run["steps"]] == ["http_request", "self_check", "declare_assertions", "finish"]
    assert run["steps"][0]["gate"][0]["action"] == "create_bill" and run["steps"][0]["reason"]
    audit = run["audit_attempts"][-1]["detail"]
    assert audit["passed"] and len(audit["checks"]) == 6 and all(c["passed"] for c in audit["checks"])
    assert "erp.erp_bills" in run["changes"] and len(run["changes"]["erp.erp_bills"]["added"]) == 1
    assert any(e["kind"] == "contract" for e in state["events"]) and any(e["kind"] == "rules" for e in state["events"])
    assert any(b["invoice_no"] == "INV-2041" for b in state["world"]["bills"]) and list(state["world"]["marks"].values()) == ["new"]
    page = console.get(f"/api/runs/{handle}/timeline")
    assert page.status_code == 200 and "INV-2041" in page.text and "Independent audit attempts" in page.text


def test_an_approval_is_answered_from_the_page(console):
    BIG = dict(method="POST", url="/erp/api/bills", json={"vendor_id": 5, "invoice_no": "INV-3107", "amount": 80000, "due_date": "2026-11-10"})
    console.holder["llm"] = FakeLLM(prep("vendor_invoice_entry") + [
        call("http_request", **BIG),
        call("ask_human", kind="approval", question="Please approve this bill"),
        call("finish", status="needs_human", summary="stopped for the test"),
    ])
    handle = console.post("/api/runs", json={"task": "Enter Zenith's latest invoice", "reset": True, "headed": False}).json()["handle"]
    pending = None
    for _ in range(100):
        pending = console.get(f"/api/runs/{handle}").json()["pending"]
        if pending:
            break
        time.sleep(0.2)
    assert pending and pending["kind"] == "approval" and any("create_bill" in c for c in pending["context"])
    assert console.post(f"/api/runs/{handle}/answer", json={"question_id": pending["id"] + 5, "reply": "approve"}).status_code == 409
    assert console.post(f"/api/runs/{handle}/answer", json={"question_id": pending["id"], "reply": "deny"}).status_code == 200
    state = wait_for(console, handle)
    assert state["status"] == "finished" and state["result"]["outcome"] == "needs_human"
    assert state["run"]["interactions"][0]["reply"] == "deny" and state["run"]["interactions"][0]["scripted"] is False


def test_the_page_is_served_self_contained(console):
    """The page must work offline (a demo machine may have no internet) and keep the elements the script in it relies on."""
    page = console.get("/")
    assert page.status_code == 200 and "VerifyOps" in page.text
    for element_id in ("presets", "task", "go", "steps", "verdict", "gate", "modal", "t-bills", "pipe", "bar"):
        assert f'id="{element_id}"' in page.text, element_id
    import re
    assert not re.search(r"""(src|href)=["']https?://""", page.text) and "url(http" not in page.text and "@import" not in page.text


def test_results_and_proof_pages(console):
    assert isinstance(console.get("/api/results").json(), list)
    proof = console.get("/api/proof").json()
    assert set(proof) == {"text", "code_unchanged"}
