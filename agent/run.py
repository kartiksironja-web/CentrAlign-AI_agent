"""Command line: `python -m agent.run --task "..." [--headed] [--faults A,B] [--reset]`.

Purpose: run the agent on one request against the mock company that is already running
(`python -m apps.company.main`) and print the final report.
Why this design: the CLI is thin. Everything is in agent.runner; this file only reads the
environment and flags, optionally sets faults / resets the world (runner-side, with the admin
token the agent never sees), shows progress, and prints the report. A real person answers
the agent's questions at the keyboard (ConsoleHuman).
How it can fail: the app must already be running and share this machine's data folder with
the auditor; without an API key in .env the model call fails with a clear LLMError.
"""
import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.text import Text

from agent.human import ConsoleHuman
from agent.llm import key_problem
from agent.loop import RunConfig
from agent.runner import Settings, apply_faults, run_task

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # report text can contain currency signs
    load_dotenv(REPO_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description="Run the AI worker on one request.")
    parser.add_argument("--task", required=True, help="the request, in plain language")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--slow", type=int, default=0, metavar="MS", help="slow the browser down by MS per action (for recording)")
    parser.add_argument("--faults", default="", help="comma separated fault flags to switch on first, e.g. DATE_TRAP")
    parser.add_argument("--reset", action="store_true", help="reset the mock company to its seeded state first")
    parser.add_argument("--audit-mode", choices=["enforce", "advisory"], default="enforce")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--max-cost", type=float, default=1.0, help="stop when the model spend reaches this many USD")
    args = parser.parse_args(argv)

    problem = key_problem(os.environ.get("AGENT_MODEL", ""))
    if problem:
        Console().print(f"[red]{problem}.[/red] Nothing was started.")
        return 1
    base_url = f"http://127.0.0.1:{os.environ.get('PORT') or 8000}"
    data_dir = Path(os.environ.get("COMPANY_DATA_DIR") or REPO_ROOT / "data")
    console = Console()
    try:
        httpx.get(base_url + "/", timeout=3)
    except httpx.HTTPError:
        console.print(f"[red]Nothing is answering at {base_url}.[/red] Start it first: python -m apps.company.main")
        return 1
    faults = [f.strip() for f in args.faults.split(",") if f.strip()]
    if faults or args.reset:
        apply_faults(base_url, os.environ.get("ADMIN_TOKEN", ""), faults, reset=args.reset)
        console.print(f"[dim]world prepared: reset={args.reset} faults={faults or 'none'}[/dim]")

    def on_event(kind: str, text: str) -> None:
        # Text objects, not markup strings: model-written text may contain [brackets]
        if kind == "step":
            console.print(Text("step ", style="dim") + Text(text))
        else:
            console.print(Text(kind, style="bold") + Text("\n" + text))

    settings = Settings(
        base_url=base_url, data_dir=data_dir, repo_root=REPO_ROOT, runs_dir=REPO_ROOT / "runs", human=ConsoleHuman(console),
        headless=not args.headed, slow_mo_ms=args.slow, on_event=on_event,
        config=RunConfig(max_steps=args.max_steps, max_seconds=args.max_seconds, max_cost_usd=args.max_cost,
                         audit_mode=args.audit_mode),
        run_info={"faults": faults},
    )
    result = run_task(args.task, settings)
    console.print(Text("\n" + result.report))
    return 0 if result.outcome.startswith("verified") else 2 if result.outcome in ("needs_human", "blocked") else 1


if __name__ == "__main__":
    sys.exit(main())
