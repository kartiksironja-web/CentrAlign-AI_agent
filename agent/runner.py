"""The composition root: wires one complete run together. Used by the CLI and the evals.

Purpose: `run_task(request, settings)` builds the gate, the browser, the toolbox, the model
client and the auditor, runs the agent loop, and cleans up.
Why this design: this is the ONLY place where the auditor meets the agent, and the agent only
ever sees it through the small AuditPort interface. The runner (not the agent) opens the
read-only database connection and takes the "before" snapshot, so there is nothing the
agent can reach: no tool opens a database, and the snapshot lives in this object.
`apply_faults` is also runner-side: it uses the admin token the agent never has and the gate
denies the agent /admin.
How it can fail: the "before" snapshot is taken when AuditorAdapter is built, i.e. right before
the agent starts; anything changed after that, by anyone, counts as a change of the run.
"""
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from agent.audit_port import AuditView, DeclareOutcome
from agent.browser import Browser
from agent.connectors import load_connectors
from agent.gate import Gate
from agent.human import Human
from agent.llm import LLM, LiteLLMClient, settings_from_env
from agent.loop import Agent, RunConfig, RunResult
from agent.pack import load_sops
from agent.policy import load_policy
from agent.runlog import RunLog
from agent.toolbox import Toolbox
from auditor.assertions import AssertionFormatError, parse_assertions_yaml
from auditor.config import AuditConfig, load_config
from auditor.engine import AuditResult, expected_problem, run_audit
from auditor.feedback import feedback
from auditor.readonly import ReadOnlyDbs
from auditor.snapshot import diff, take_snapshot
from auditor.validate import Requirements, validate


class AuditorAdapter:
    """Implements AuditPort with the real auditor. Holds the read-only databases and the before-snapshot."""

    def __init__(self, config: AuditConfig):
        self.dbs = ReadOnlyDbs(config)
        self.before = take_snapshot(self.dbs)

    def declare(self, yaml_text: str, done_requires: dict, allow_literal: dict) -> DeclareOutcome:
        try:
            assertions = parse_assertions_yaml(yaml_text)
        except AssertionFormatError as e:
            return DeclareOutcome(False, "ASSERTIONS NOT ACCEPTED (format):\n" + "\n".join(f"- {m}" for m in e.errors))
        changes = diff(self.before, take_snapshot(self.dbs))
        errors = validate(assertions, self.dbs, Requirements(done_requires, allow_literal), changes, expected_problem, declaring=True)
        if errors:
            return DeclareOutcome(False, "ASSERTIONS NOT ACCEPTED:\n" + "\n".join(f"- {m}" for m in errors))
        ids = ", ".join(a.id for a in assertions)
        return DeclareOutcome(True, f"Assertions accepted ({len(assertions)} checks: {ids}). "
                                    "An independent audit will check them when you finish.")

    def audit(self, yaml_text: str, done_requires: dict, allow_literal: dict, round_no: int) -> AuditView:
        result = run_audit(self.dbs, parse_assertions_yaml(yaml_text), Requirements(done_requires, allow_literal), self.before)
        return AuditView(result.valid, result.passed, feedback(result, round_no), feedback(result, 3), result.as_dict())

    def changes(self) -> dict:
        return AuditResult([], [], diff(self.before, take_snapshot(self.dbs))).as_dict()["changes"]

    def close(self) -> None:
        self.dbs.close()


@dataclass
class Settings:
    base_url: str
    data_dir: Path
    repo_root: Path
    runs_dir: Path
    human: Human
    pack_dir: Path | None = None  # default: <repo_root>/company_pack
    headless: bool = True
    slow_mo_ms: int = 0
    config: RunConfig = field(default_factory=RunConfig)
    llm: LLM | None = None  # default: LiteLLM client configured from the environment
    use_browser: bool = True
    chromium: object | None = None  # an already-running Chromium (tests share one); default: launch one
    run_info: dict = field(default_factory=dict)
    on_event: object | None = None
    clock: object | None = None


def apply_faults(base_url: str, admin_token: str, faults: list[str], reset: bool = False) -> None:
    """Reset the world and/or set fault flags through the admin endpoints (runner only)."""
    headers = {"X-Admin-Token": admin_token}
    if reset:
        response = httpx.post(f"{base_url}/admin/reset", headers=headers, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"reset failed: HTTP {response.status_code} {response.text[:200]}")
    response = httpx.post(f"{base_url}/admin/faults", headers=headers, json={"faults": faults}, timeout=10)
    if response.status_code != 200:
        raise RuntimeError(f"setting faults failed: HTTP {response.status_code} {response.text[:200]}")


def run_task(request: str, s: Settings) -> RunResult:
    pack = s.pack_dir or s.repo_root / "company_pack"
    policy = load_policy(pack / "policy.yaml")
    ids = [c.id for c in load_connectors(pack / "connectors")]
    connectors = load_connectors(pack / "connectors", {i: s.base_url for i in ids})
    gate = Gate(policy, connectors)
    sops = load_sops(pack / "sops")
    llm = s.llm or LiteLLMClient(**settings_from_env())

    adapter = AuditorAdapter(load_config(pack / "audit.yaml", {"data": s.data_dir, "repo": s.repo_root}))
    browser = None
    try:
        if s.use_browser:
            browser = Browser(gate, s.chromium) if s.chromium else Browser.launch(gate, headless=s.headless, slow_mo_ms=s.slow_mo_ms)
        toolbox = Toolbox(gate, s.human, default_base_url=s.base_url, browser=browser,
                          document_roots={"inputs": s.repo_root / "inputs"})
        kwargs = {"clock": s.clock} if s.clock else {}
        agent = Agent(
            llm=llm, toolbox=toolbox, gate=gate, sops=sops, systems_text="\n\n".join(c.describe() for c in connectors),
            audit=adapter, runlog=RunLog(s.runs_dir), config=s.config,
            run_info={"base_url": s.base_url, "human": "scripted" if s.human.scripted else "console",
                      "temperature": getattr(llm, "temperature", None), **s.run_info},
            on_event=s.on_event, **kwargs,
        )
        return agent.run(request)
    finally:
        if browser is not None:
            browser.close()
        adapter.close()
