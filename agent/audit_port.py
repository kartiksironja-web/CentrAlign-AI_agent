"""The loop's view of the auditor: a small interface, so the loop never imports the auditor.

Purpose: three questions the loop may ask. Can these assertions be accepted (`declare`)?
Did the work really happen (`audit`)? What did the run change in the company systems
(`changes`)?
Why this design: the auditor must stay independent of the agent. The loop holds an AuditPort
supplied by the runner; the runner owns the read-only connection and the "before" snapshot,
which the agent can never touch. Tests give the loop the real adapter (runner.py) or a stub.
How it can fail: `audit` judges only what the assertions say; see the auditor package.
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class DeclareOutcome:
    ok: bool
    message: str  # shown to the model


@dataclass
class AuditView:
    valid: bool  # the assertions were acceptable (not malformed or too weak)
    passed: bool
    feedback: str  # what the model is told for this round
    full: str  # the complete report, for the human on escalation
    detail: dict = field(default_factory=dict)  # JSON-safe auditor result: checks and changes


class AuditPort(Protocol):
    def declare(self, yaml_text: str, done_requires: dict, allow_literal: dict) -> DeclareOutcome: ...

    def audit(self, yaml_text: str, done_requires: dict, allow_literal: dict, round_no: int) -> AuditView: ...

    def changes(self) -> dict:
        """Rows added / changed / removed in the company systems since the run started."""
        ...
