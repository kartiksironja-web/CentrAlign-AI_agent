"""Runs started from the web console: one agent run in a background thread, a human who answers in the browser.

Purpose: `RunManager.start` runs the real agent (agent.runner.run_task, unchanged) in a thread and keeps what a web page needs to
show it live: the events the loop emits, the run log the loop rewrites after every step, a question waiting for a person, and the
company's data. `WebHuman` is the console's `Human`: `ask()` blocks the agent until the person clicks Approve/Deny or types an answer.
Why this design: the console is a viewer and a keyboard; it contains no agent logic. Everything it shows is read from the same run.json
the command line produces, so what you see on screen is what the audit trail says. One run at a time, because the company data is shared.
How it can fail: a run that never gets an answer waits (up to the timeout) and then hands over as "no human available"; the model key
missing or a quota used up appears as the run's error; live data is read straight from the SQLite file (read-only), so it is only as
fresh as the last commit by the company app.
"""
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agent.human import NoHumanReply
from agent.loop import RunConfig
from agent.runner import Settings, apply_faults, run_task


class Busy(Exception):
    """A run is already in progress."""


class WebHuman:
    """Blocks the agent in `ask` until `answer` is called from the web page."""
    scripted = False

    def __init__(self, wait_seconds: float = 1800.0):
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._wait, self._counter, self._reply, self._closed = wait_seconds, 0, "", False
        self.pending: dict | None = None

    def ask(self, kind, question, options, context):
        with self._lock:
            self._counter += 1
            self.pending = {"id": self._counter, "kind": kind, "question": question,
                            "options": list(options or []), "context": list(context or [])}
            self._reply = ""
            self._event.clear()
        answered = self._event.wait(self._wait) and not self._closed
        with self._lock:
            reply, self.pending = self._reply, None
        if not answered:
            raise NoHumanReply("nobody answered in the console")
        return reply

    def answer(self, question_id: int, reply: str) -> bool:
        with self._lock:
            if self.pending is None or self.pending["id"] != question_id:
                return False
            self._reply = reply
        self._event.set()
        return True

    def close(self) -> None:
        self._closed = True
        self._event.set()


@dataclass
class Run:
    handle: str
    task: str
    faults: list[str]
    audit_mode: str
    runs_dir: Path
    human: WebHuman
    started: float = field(default_factory=time.time)
    finished: float | None = None
    status: str = "starting"  # starting | running | finished | error
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    before_bills: dict | None = None  # the bills when the run began; None until then (so nothing is marked "new" too early)

    def add_event(self, kind: str, text: str) -> None:
        self.events.append({"t": round(time.time() - self.started, 1), "kind": kind, "text": text})

    @property
    def active(self) -> bool:
        return self.status in ("starting", "running")


_LOG_LOCK = threading.Lock()


def install_log_lock() -> None:
    """Make the agent's write of run.json and the console's reads of it take turns (within this process).

    The agent rewrites run.json after every step with os.replace. On Windows that fails with "Access is denied" if ANY handle to the
    file is open at that instant, whatever sharing flags the reader used (seen once in the evaluation, caused by a monitoring
    script). The console polls the file every second, so it must never read while the agent replaces it. Wrapping RunLog.write
    here, from the console, keeps agent/ untouched."""
    from agent.runlog import RunLog

    if getattr(RunLog.write, "_serialized", False):
        return
    original = RunLog.write

    def write(self):
        with _LOG_LOCK:
            return original(self)

    write._serialized = True
    RunLog.write = write


def read_run_json(runs_dir: Path) -> dict | None:
    """The run log of the (single) run folder under runs_dir, or None before the first write."""
    for path in sorted(runs_dir.glob("*/run.json")):
        try:
            with _LOG_LOCK:
                text = path.read_text(encoding="utf-8")
            return json.loads(text)
        except (OSError, ValueError):
            return None  # not complete yet: the next poll will see it
    return None

def read_tables(data_dir: Path) -> dict:
    """The company's tables, read-only, for the live panel."""
    path = data_dir / "company.db"
    if not path.exists():
        return {"vendors": [], "bills": [], "invoices": []}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        def rows(sql):
            return [dict(r) for r in con.execute(sql)]
        return {
            "vendors": rows("SELECT id, name, city, payment_terms_days FROM erp_vendors ORDER BY id"),
            "bills": rows("SELECT id, vendor_id, invoice_no, amount, due_date, status, created_via FROM erp_bills ORDER BY id"),
            "invoices": rows("SELECT invoice_no, vendor_id, invoice_date, amount, due_date, revised_of FROM portal_invoices ORDER BY invoice_date DESC"),
        }
    finally:
        con.close()


class RunManager:
    def __init__(self, repo_root: Path, base_url: str, data_dir: Path, admin_token: str = "", settings_hook=None,
                 runs_root: Path | None = None):
        self.repo_root, self.base_url, self.data_dir, self.admin_token = repo_root, base_url, data_dir, admin_token
        self.runs_root = runs_root or repo_root / "runs" / "console"
        self.settings_hook = settings_hook  # tests use it to inject a scripted model
        self.runs: dict[str, Run] = {}

    def active_run(self) -> Run | None:
        return next((r for r in self.runs.values() if r.active), None)

    def start(self, task: str, faults: list[str], audit_mode: str, headed: bool, slow_ms: int, reset: bool) -> Run:
        if self.active_run():
            raise Busy("a run is already in progress")
        handle = uuid.uuid4().hex[:8]
        run = Run(handle, task, faults, audit_mode, self.runs_root / handle, WebHuman())
        self.runs[handle] = run
        threading.Thread(target=self._work, args=(run, headed, slow_ms, reset), daemon=True, name=f"run-{handle}").start()
        return run

    def _work(self, run: Run, headed: bool, slow_ms: int, reset: bool) -> None:
        try:
            install_log_lock()
            run.status = "running"
            apply_faults(self.base_url, self.admin_token, run.faults, reset=reset)  # runner-side, with the admin token only the runner has
            run.before_bills = {b["id"]: b for b in read_tables(self.data_dir)["bills"]}
            settings = Settings(
                base_url=self.base_url, data_dir=self.data_dir, repo_root=self.repo_root, runs_dir=run.runs_dir, human=run.human,
                headless=not headed, slow_mo_ms=slow_ms, on_event=run.add_event,
                config=RunConfig(audit_mode=run.audit_mode), run_info={"faults": run.faults, "via": "web console"},
            )
            if self.settings_hook:
                self.settings_hook(settings)
            result = run_task(run.task, settings)
            run.result = {"outcome": result.outcome, "report": result.report, "run_id": result.run_id}
            run.status = "finished"
        except Exception as e:  # shown to the person; the details of a model error never include the key
            run.error = f"{type(e).__name__}: {str(e)[:500]}"
            run.status = "error"
        finally:
            run.finished = time.time()
            run.human.close()

    def snapshot(self, run: Run) -> dict:
        tables = read_tables(self.data_dir)
        marks = {}
        if run.before_bills is not None:
            for bill in tables["bills"]:
                before = run.before_bills.get(bill["id"])
                if before is None:
                    marks[bill["id"]] = "new"
                elif before != bill:
                    marks[bill["id"]] = "changed"
        return {
            "handle": run.handle, "task": run.task, "faults": run.faults, "audit_mode": run.audit_mode, "status": run.status,
            "elapsed": round((run.finished or time.time()) - run.started, 1), "error": run.error, "result": run.result,
            "pending": run.human.pending, "events": run.events, "run": read_run_json(run.runs_dir),
            "world": {**tables, "marks": marks},
        }
