"""The feedback ladder: how much a failed audit tells the agent.

Purpose: turn an AuditResult into text for the agent, revealing more with each failed round.
  round 1  failed assertion ids and the field/table names only
  round 2  adds expected vs actual values
  round 3+ (escalation to a human) everything: details and what changed in the run
Why this design: if the audit handed over the answer immediately, the agent would
copy it and "diagnosing the problem" would not be measured; and an agent that
could query the auditor freely would turn it into an oracle. A validation problem
(a malformed or too-weak assertion) is always shown in full: it says nothing
about the true values.
How it can fail: the field names in round 1 are a hint by design; they are not a secret.
"""
from auditor.engine import AuditResult, CheckResult


def _where(c: CheckResult) -> str:
    if c.field:
        return f"field '{c.field}' of table '{c.table}'"
    if c.table:
        return f"table '{c.table}'"
    return "the whole run"


def feedback(result: AuditResult, round_no: int) -> str:
    if not result.valid:
        lines = [f"AUDIT NOT RUN: {len(result.validation_errors)} problem(s) with the declared assertions:"]
        lines += [f"- {e}" for e in result.validation_errors]
        return "\n".join(lines)

    lines = [f"AUDIT FAILED (feedback round {round_no}). Failed checks:"]
    for c in result.failed:
        lines.append(f"- {c.id} ({c.kind}): {_where(c)}")
        if round_no >= 2:
            lines.append(f"    expected: {c.expected!r}   actual: {c.actual!r}")
        if round_no >= 3 and c.detail:
            lines.append(f"    detail: {c.detail}")
    if round_no >= 3:
        lines.append(f"Changes made during the run: {result.diff.summary() or 'none'}")
    if round_no == 1:
        lines.append(
            "Values are not shown. Re-read the saved records yourself, compare them with the "
            "source documents, find what differs and repair it."
        )
    return "\n".join(lines)
