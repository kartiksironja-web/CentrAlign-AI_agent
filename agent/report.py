"""The final report: what the requester reads when the run ends.

Purpose: outcome, what was done, evidence, anything needing attention, run id.
Why this design: the outcome line, the evidence and the list of changes are written by CODE
from what the auditor and the run state recorded. The model's summary appears only in its own
labelled section ("not verified"). So the report cannot say "done" because the model said
"done"; and what changed in the systems is what was observed, not what the model reports.
How it can fail: it can only show what was recorded. If no audit ran (the agent did not claim
completion), the report says so and shows just the observed changes.
"""
import json

OUTCOME_TEXT = {
    "verified_done": "DONE and VERIFIED: an independent audit of the real systems passed.",
    "verified_no_action": "NO ACTION NEEDED, verified: the independent audit passed and the work was already in place.",
    "needs_human": "NEEDS A HUMAN: the agent stopped and handed this back to you.",
    "blocked": "BLOCKED: the agent could not proceed.",
    "audit_failed": "NOT VERIFIED: the agent claimed the work was done but the independent audit failed. A human must check.",
    "false_completion": "NOT VERIFIED (advisory mode): the agent's first claim of success was wrong according to the audit.",
    "budget_exhausted": "NOT COMPLETED: the agent ran out of its budget.",
    "error": "ERROR: the run stopped because of a technical problem.",
}


def _short(value, limit=160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _check_line(check: dict) -> str:
    mark = "PASS" if check["passed"] else "FAIL"
    where = f"{check['table']}.{check['field']}" if check.get("field") else (check.get("table") or "whole run")
    if check["kind"] == "field_equals":
        if check["passed"]:
            return f"  {mark}  {check['id']}: {where} = {check['actual']!r}, matches the independent source"
        return f"  {mark}  {check['id']}: {where} is {check['actual']!r}, the source says {check['expected']!r}" + (
            f" ({check['detail']})" if check.get("detail") else "")
    detail = f" ({check['detail']})" if check.get("detail") else ""
    if check["kind"] == "no_other_changes":
        if check["passed"]:
            return f"  {mark}  {check['id']}: nothing changed beyond the declared changes"
        return f"  {mark}  {check['id']}: unexpected changes: {check['actual']}"
    return f"  {mark}  {check['id']}: {check['kind']} on {where}: {check['actual']!r} (expected {check['expected']!r}){detail}"


def render_changes(changes: dict) -> list[str]:
    if not changes:
        return ["  nothing was added, changed or removed in the company systems"]
    lines = []
    for table, change in changes.items():
        counts = ", ".join(f"{len(change[k])} {k}" for k in ("added", "changed", "removed") if change[k])
        lines.append(f"  {table}: {counts}")
        lines += [f"    + {_short(row)}" for row in change["added"]]
        lines += [f"    ~ was {_short(c['before'])}  now {_short(c['after'])}" for c in change["changed"]]
        lines += [f"    - {_short(row)}" for row in change["removed"]]
    return lines


def render_report(*, run_id: str, request: str, outcome: str, detail: str, agent_summary: str | None,
                  audit: dict | None, changes: dict, attention: list[str], metrics: dict) -> str:
    text = OUTCOME_TEXT[outcome] + (f" ({detail})" if detail else "")
    lines = [f"VerifyOps report, run {run_id}", f"Request: {request}", "", f"OUTCOME: {text}", ""]
    lines.append("What the agent says it did (its own words, NOT verified):")
    lines.append("  " + (agent_summary.strip().replace("\n", "\n  ") if agent_summary else "(the agent did not finish)"))
    lines += ["", "Evidence from the independent audit (code, not the agent):"]
    if audit is None:
        lines.append("  no audit was run, because the agent did not claim the work was complete")
    elif audit["validation_errors"]:
        lines += [f"  the declared checks were rejected: {e}" for e in audit["validation_errors"]]
    else:
        lines += [_check_line(c) for c in audit["checks"]]
    lines += ["", "Changes observed in the company systems:"] + render_changes(changes)
    lines += ["", "Needs attention:"] + ([f"  - {a}" for a in attention] or ["  nothing"])
    lines += ["", f"Run: {metrics['steps']} steps, {metrics['wall_seconds']:.0f} s, "
                  f"{metrics['tokens_in']} tokens in / {metrics['tokens_out']} out"
                  + (f", about ${metrics['cost_usd']:.3f}" if metrics.get("cost_usd") is not None else ", cost unknown")
                  + f". Details: runs/{run_id}/run.json"]
    return "\n".join(lines)
