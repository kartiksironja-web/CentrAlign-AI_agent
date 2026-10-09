"""Run the evaluation scenarios: `python -m evals.run_evals --repeats 3`.

Purpose: for every scenario and every repeat: a fresh copy of the seeded data, a fresh mock
company server on a free port with the scenario's faults, one agent run with a scripted human,
a ground-truth check of the final database, and one record of metrics. Then `evals/RESULTS.md`
is generated from the records.
Why this design: nothing is shared between runs (own data folder, own server process, own
browser context), so one run cannot affect the next; the seed is deterministic, so a scenario
differs between repeats only through the model. Real model calls cost money: the batch stops
starting new runs once the spend limit is reached and says which runs it skipped.
`--results-from` regenerates RESULTS.md from a stored batch without running anything.
How it can fail: a run that crashes for infrastructure reasons is recorded as a failed run
(outcome harness_error), never dropped; the cost limit only works for models LiteLLM can price.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from agent.human import ScriptedHuman
from agent.llm import LiteLLMClient, key_problem, settings_from_env
from agent.loop import RunConfig
from agent.runner import Settings, run_task
from apps.company.seed import seed_database
from evals import ground_truth
from evals.metrics import derive_record, failed_record
from evals.results import render_results
from evals.scenarios import Scenario, load_scenarios, select

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_TOKEN = "eval-admin-token"  # mock credential for the throwaway servers


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_app(data_dir: Path, faults: list[str], port: int, log_path: Path, repo_root: Path) -> subprocess.Popen:
    env = {**os.environ, "COMPANY_DATA_DIR": str(data_dir), "FAULTS": ",".join(faults), "PORT": str(port),
           "ADMIN_TOKEN": ADMIN_TOKEN}
    log = open(log_path, "wb")
    proc = subprocess.Popen([sys.executable, "-m", "apps.company.main", "--port", str(port), "--quiet"],
                            cwd=repo_root, env=env, stdout=log, stderr=log)
    proc.log_handle = log  # closed in stop_app; an open handle would block deleting the temp folder on Windows
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the mock company exited early: {log_path.read_text(errors='replace')[-400:]}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("the mock company did not start within 20 s")


def stop_app(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    handle = getattr(proc, "log_handle", None)
    if handle is not None:
        handle.close()


def attach_record(log_path: str, record: dict) -> None:
    """Put the evaluation record into the run's own run.json, next to what the agent did."""
    path = Path(log_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["eval"] = record
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temp, path)


def run_one(scenario: Scenario, repeat: int, *, template: Path, llm_factory, meta: dict, repo_root: Path,
            runs_dir: Path, chromium, use_browser: bool, limits: RunConfig) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="verifyops-eval-"))
    proc = None
    try:
        data_dir = tmp / "data"
        shutil.copytree(template, data_dir)  # a fresh copy of the seeded world for every run
        port = free_port()
        proc = start_app(data_dir, scenario.faults, port, tmp / "app.log", repo_root)
        # The starting world is what exists once the faults are in place (a fault may add rows),
        # right before the agent starts: the same moment the auditor takes its own snapshot.
        before = ground_truth.read_world(data_dir / "company.db")
        spec = scenario.scripted_human
        result = run_task(scenario.task, Settings(
            base_url=f"http://127.0.0.1:{port}", data_dir=data_dir, repo_root=repo_root, runs_dir=runs_dir,
            human=ScriptedHuman({"clarification": spec.clarification, "approval": spec.approval}),
            use_browser=use_browser, chromium=chromium, llm=llm_factory(scenario, repeat),
            config=RunConfig(max_steps=limits.max_steps, max_seconds=limits.max_seconds, max_cost_usd=limits.max_cost_usd,
                             audit_mode=scenario.audit_mode),
            run_info={"scenario": scenario.id, "repeat": repeat, "batch": meta["batch"], "faults": scenario.faults,
                      "seed_variant": scenario.seed_variant, "human": "SCRIPTED_HUMAN"}))
        stop_app(proc)
        proc = None
        ground = ground_truth.check(scenario.expect.ground_truth, before, data_dir / "company.db")
        run = json.loads(Path(result.log_path).read_text(encoding="utf-8"))
        record = derive_record(run, scenario, ground, meta)
        attach_record(result.log_path, record)
        return record
    except Exception as e:  # an infrastructure failure is a recorded failed run, not a missing one
        return failed_record(scenario, meta, "harness_error", f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        if proc is not None:
            stop_app(proc)
        shutil.rmtree(tmp, ignore_errors=True)


def run_batch(scenarios: list[Scenario], *, repeats: int, repo_root: Path, runs_dir: Path, llm_factory, chromium=None,
              use_browser: bool = True, limits: RunConfig | None = None, budget_usd: float | None = None,
              batch_id: str | None = None, progress=print) -> tuple[dict, list[dict]]:
    limits = limits or RunConfig()
    batch_id = batch_id or "eval-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    started = datetime.now().isoformat(timespec="seconds")
    template = Path(tempfile.mkdtemp(prefix="verifyops-template-"))
    try:
        seed_database(template / "data")
        template_data = template / "data"
        records, spent, unpriced = [], 0.0, False
        for scenario in scenarios:
            for repeat in range(1, repeats + 1):
                meta = {"batch": batch_id, "repeat": repeat}
                if budget_usd is not None and spent >= budget_usd:
                    records.append(failed_record(scenario, meta, "skipped_budget", "spend limit reached"))
                    continue
                progress(f"[{batch_id}] {scenario.id} run {repeat}/{repeats} ...")
                record = run_one(scenario, repeat, template=template_data, llm_factory=llm_factory,
                                 meta=meta, repo_root=repo_root, runs_dir=runs_dir, chromium=chromium,
                                 use_browser=use_browser, limits=limits)
                records.append(record)
                if record["cost_usd"] is None:
                    unpriced = True
                else:
                    spent += record["cost_usd"]
                progress(f"    -> {record['outcome']}; meets expectations: {record['meets_expectations']}; "
                         f"steps {record['steps']}; spent so far ${spent:.2f}")
        limit_text = (f"{limits.max_steps} steps, {limits.max_seconds:.0f} s and ${limits.max_cost_usd} per run"
                      + (f"; ${budget_usd} for the whole batch" if budget_usd is not None else "")
                      + ("; cost unknown for this model, so the spend limit could not be enforced" if unpriced else ""))
        first = next((r for r in records if r.get("model")), {})  # the model is read from what the runs recorded
        batch = {"batch_id": batch_id, "started_at": started, "finished_at": datetime.now().isoformat(timespec="seconds"),
                 "model": first.get("model", "unknown (no run completed)"), "temperature": first.get("temperature"), "repeats": repeats,
                 "scenarios": [s.id for s in scenarios], "command": " ".join(sys.argv), "limits": limit_text,
                 "total_cost_usd": None if unpriced else spent}
        return batch, records
    finally:
        shutil.rmtree(template, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(REPO_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="Run the evaluation scenarios and write evals/RESULTS.md.")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", default="", help="comma separated ids (default: all)")
    parser.add_argument("--budget-usd", type=float, default=15.0, help="stop starting runs after this much model spend")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--max-cost", type=float, default=1.0, help="per-run model spend limit in USD")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals" / "RESULTS.md")
    parser.add_argument("--results-from", type=Path, help="regenerate RESULTS.md from a stored batch; run nothing")
    args = parser.parse_args(argv)

    if args.results_from:
        stored = json.loads(args.results_from.read_text(encoding="utf-8"))
        args.out.write_text(render_results(stored["batch"], stored["records"]), encoding="utf-8")
        print(f"Wrote {args.out} from {args.results_from}")
        return 0

    settings = settings_from_env()
    problem = key_problem(settings["model"])
    if problem:
        print(f"{problem}. No runs were started.")
        return 1
    scenarios = select(load_scenarios(REPO_ROOT / "evals" / "scenarios.yaml"), [s for s in args.scenarios.split(",") if s])
    total = len(scenarios) * args.repeats
    print(f"{len(scenarios)} scenario(s) x {args.repeats} = {total} real model runs on {settings['model']}; "
          f"stops starting new runs after ${args.budget_usd} of list-price cost (a free tier charges nothing, "
          f"but has request limits: see .env.example).")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=not args.headed)
        try:
            batch, records = run_batch(
                scenarios, repeats=args.repeats, repo_root=REPO_ROOT, runs_dir=REPO_ROOT / "runs",
                llm_factory=lambda s, r: LiteLLMClient(**settings), chromium=chromium, budget_usd=args.budget_usd,
                limits=RunConfig(max_steps=args.max_steps, max_seconds=args.max_seconds, max_cost_usd=args.max_cost))
        finally:
            chromium.close()
    results_dir = REPO_ROOT / "evals" / "results"
    results_dir.mkdir(exist_ok=True)
    stored = results_dir / f"{batch['batch_id']}.json"
    stored.write_text(json.dumps({"batch": batch, "records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
    args.out.write_text(render_results(batch, records), encoding="utf-8")
    print(f"Wrote {stored} and {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
