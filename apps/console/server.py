"""The console's web server: the API behind the page in apps/console/static/index.html.

Purpose: start a run of the real agent from the browser, report its state while it works, take the person's answers to the agent's
questions, reset the company data, and show the generated evaluation results and the generalization proof.
Why this design: the page polls one endpoint (`GET /api/runs/<handle>`) that returns everything it needs, so the page is plain
HTML and JavaScript with no build step and no outside requests. The server only starts runs and reads files: the agent, gate and
auditor run exactly as they do from the command line.
How it can fail: it binds to 127.0.0.1 only (a local tool, no login); only one run at a time; if the company app is not running,
starting a run says so instead of failing half way.
"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.llm import key_problem
from agent.runner import apply_faults
from apps.console.markdown import to_html
from apps.console.runs import Busy, RunManager, read_run_json, read_tables
from evals.scenarios import load_scenarios
from scripts.prove_generalization import report as generalization_report
from scripts.render_run import render_html

HERE = Path(__file__).resolve().parent
FAULTS = ["DATE_TRAP", "SAVE_BROKEN", "BUTTON_RENAME", "DUPLICATE_VENDOR"]
RESULT_FILES = [("Frozen scenarios S1-S5", "evals/RESULTS.md"), ("New tasks S6, S7, S10", "evals/RESULTS_PHASE7.md"),
                ("S6 again after a handbook-only fix", "evals/RESULTS_PHASE7_S6_AFTER_HANDBOOK_FIX.md")]


class StartRun(BaseModel):
    task: str = Field(min_length=3, max_length=2000)
    faults: list[str] = []
    audit_mode: str = "enforce"
    headed: bool = True
    slow_ms: int = Field(default=250, ge=0, le=3000)
    reset: bool = True


class Answer(BaseModel):
    question_id: int
    reply: str = Field(min_length=1, max_length=2000)


class Reset(BaseModel):
    faults: list[str] = []


def create_app(repo_root: Path | None = None, base_url: str | None = None, data_dir: Path | None = None,
               admin_token: str | None = None, settings_hook=None, runs_root: Path | None = None) -> FastAPI:
    repo_root = repo_root or HERE.parents[1]
    base_url = base_url or f"http://127.0.0.1:{os.environ.get('PORT') or 8000}"
    data_dir = data_dir or Path(os.environ.get("COMPANY_DATA_DIR") or repo_root / "data")
    admin_token = os.environ.get("ADMIN_TOKEN", "") if admin_token is None else admin_token
    manager = RunManager(repo_root, base_url, data_dir, admin_token, settings_hook, runs_root)
    app = FastAPI(title="VerifyOps console", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def company_up() -> bool:
        try:
            httpx.get(base_url + "/", timeout=2)
            return True
        except httpx.HTTPError:
            return False

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(HERE / "static" / "index.html")

    @app.get("/api/config")
    def config():
        model = os.environ.get("AGENT_MODEL", "")
        scenarios = load_scenarios(repo_root / "evals" / "scenarios.yaml")
        return {
            "model": model, "model_problem": key_problem(model) if model else "AGENT_MODEL is not set", "company_url": base_url,
            "company_up": company_up(), "faults": FAULTS,
            "presets": [{"id": s.id, "name": s.name, "task": s.task, "faults": s.faults, "audit_mode": s.audit_mode,
                         "human": "approval" if s.scripted_human.approval else "clarification" if s.scripted_human.clarification else ""}
                        for s in scenarios],
            "active": manager.active_run().handle if manager.active_run() else None,
        }

    @app.post("/api/runs")
    def start(body: StartRun):
        if body.audit_mode not in ("enforce", "advisory"):
            raise HTTPException(422, "audit_mode must be enforce or advisory")
        unknown = [f for f in body.faults if f not in FAULTS]
        if unknown:
            raise HTTPException(422, f"unknown fault flags: {unknown}")
        if not company_up():
            raise HTTPException(503, f"the company app is not answering at {base_url}; start it with: python -m apps.company.main")
        try:
            run = manager.start(body.task.strip(), body.faults, body.audit_mode, body.headed, body.slow_ms, body.reset)
        except Busy:
            raise HTTPException(409, "a run is already in progress")
        return {"handle": run.handle}

    def run_or_404(handle: str):
        run = manager.runs.get(handle)
        if run is None:
            raise HTTPException(404, "no such run")
        return run

    @app.get("/api/runs/{handle}")
    def state(handle: str):
        return manager.snapshot(run_or_404(handle))

    @app.post("/api/runs/{handle}/answer")
    def answer(handle: str, body: Answer):
        run = run_or_404(handle)
        if not run.human.answer(body.question_id, body.reply.strip()):
            raise HTTPException(409, "that question is no longer waiting for an answer")
        return {"ok": True}

    @app.get("/api/runs/{handle}/timeline", response_class=HTMLResponse)
    def timeline(handle: str):
        log = read_run_json(run_or_404(handle).runs_dir)
        if log is None:
            raise HTTPException(404, "the run has not written its log yet")
        return render_html(log)

    @app.get("/api/world")
    def world():
        return {**read_tables(data_dir), "company_up": company_up()}

    @app.post("/api/world/reset")
    def reset(body: Reset):
        if manager.active_run():
            raise HTTPException(409, "a run is in progress")
        if not company_up():
            raise HTTPException(503, "the company app is not answering")
        apply_faults(base_url, admin_token, [f for f in body.faults if f in FAULTS], reset=True)
        return {"ok": True}

    @app.get("/api/results")
    def results():
        out = []
        for title, rel in RESULT_FILES:
            path = repo_root / rel
            if path.exists():
                out.append({"title": title, "file": rel, "html": to_html(path.read_text(encoding="utf-8"))})
        return out

    @app.get("/api/proof")
    def proof():
        text, unchanged = generalization_report(repo_root)
        return {"text": text, "code_unchanged": unchanged}

    return app
