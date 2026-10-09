"""The run state, and how it becomes the model's context at every step.

Purpose: RunState holds everything the loop knows (request, contract, handbook, plan versions,
notes, steps, checks, audits, budgets). `render_state` turns it into the single message the
model sees at each step.
Why this design: STATE, NOT TRANSCRIPT. The model is not shown an ever-growing chat. Each
step it gets the stable facts (request, contract, rules, systems, plan, notes), the budget,
the last three steps IN FULL (what it did and exactly what came back) and one line for each
older step. So the context stays about the same size at step 30 as at step 5, and what the
model "remembers" is only what it chose to note or what is in the plan.
How it can fail: anything the model needed from an older step and did not note is gone
(only a one-line summary remains). That is deliberate pressure to use `note`.
"""
from dataclasses import dataclass, field

from agent.contract import TaskContract
from agent.llm import Usage
from agent.notes import Notes
from agent.pack import Sop

VERBATIM_STEPS = 3


@dataclass
class PlanItem:
    text: str
    done: bool = False


@dataclass
class PlanVersion:
    version: int
    items: list[PlanItem]
    reason: str
    step: int  # 0 = the initial plan, before any step


@dataclass
class StepRecord:
    n: int
    kind: str  # tool | control | nudge | refused | invalid
    tool: str
    args: dict
    reason: str
    result_text: str
    summary: str
    gate: list[dict] = field(default_factory=list)
    detectors: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    ms: int = 0
    error: bool = False


@dataclass
class RunState:
    request: str
    contract: TaskContract | None = None
    sops: list[Sop] = field(default_factory=list)
    systems_text: str = ""
    plans: list[PlanVersion] = field(default_factory=list)
    notes: Notes = field(default_factory=Notes)
    steps: list[StepRecord] = field(default_factory=list)
    self_checks: list[dict] = field(default_factory=list)
    assertion_versions: list[dict] = field(default_factory=list)
    audit_attempts: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    llm_calls: list[dict] = field(default_factory=list)
    needs_clarification: bool = False
    forced_reason: str | None = None  # set when only ask_human / finish are allowed any more
    human_unavailable: bool = False
    nudges: int = 0
    refusals: int = 0
    audit_failures: int = 0
    audit_feedback: str | None = None  # what the audit told the agent when it last rejected a completion claim
    writes_made: list[dict] = field(default_factory=list)  # writes the gate let through, from the gate's own log
    first_claim: dict | None = None
    finish: dict | None = None
    outcome: str | None = None
    detail: str = ""  # why the outcome is what it is (budget reason, error text)

    @property
    def plan(self) -> PlanVersion | None:
        return self.plans[-1] if self.plans else None

    @property
    def assertions_yaml(self) -> str | None:
        return self.assertion_versions[-1]["yaml"] if self.assertion_versions else None


def _call_line(step: StepRecord) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in step.args.items())
    return f"{step.tool}({args[:300]})"


def _verification(state: RunState) -> str:
    """Where the agent stands on checking its work. Kept in the state because the step that reported it
    scrolls out of the 'last steps' window; without this a real model declared the same assertions nine
    times because it could no longer see that they had been accepted."""
    lines = []
    if state.self_checks:
        last = state.self_checks[-1]
        lines.append(f"- self-check (step {last['step']}): " + ("matched the source" if last["passed"] else "DID NOT MATCH: fix it"))
    if state.assertion_versions:
        last = state.assertion_versions[-1]
        lines.append(f"- assertions ACCEPTED at step {last['step']} (version {len(state.assertion_versions)}). The independent "
                     f"audit checks them when you call finish. Declare again only if you must change them:\n{last['yaml']}")
    if state.audit_feedback:
        lines.append("- the independent audit REJECTED your last completion claim:\n" + state.audit_feedback)
    elif state.assertion_versions and state.self_checks and state.self_checks[-1]["passed"]:
        lines.append("- NEXT STEP: everything needed to finish is in place. Call `finish` now, unless you have found a "
                     "real problem with the work itself. More browsing and re-reading will not change the audit.")
    return "\n".join(lines)


def render_state(state: RunState, budget_line: str) -> str:
    parts = [f"# Request\n{state.request}"]

    understanding = state.contract.render() if state.contract else "(not prepared)"
    if state.needs_clarification:
        understanding += ("\nYOU MUST FIRST ask the requester these questions with ask_human(kind=clarification); "
                          "no other tool is allowed until you have.")
    parts.append(f"# Your understanding\n{understanding}")

    parts.append(f"# Systems you can use\n{state.systems_text}")

    if state.sops:
        rules = "\n\n".join(f"## {s.title} (id: {s.id})\n{s.body}" for s in state.sops)
    else:
        rules = "(no company rule was selected for this request)"
    parts.append(f"# Company rules that apply\n{rules}")

    if state.plan:
        marks = "\n".join(f"[{'x' if i.done else ' '}] {i.text}" for i in state.plan.items)
        parts.append(f"# Plan (version {state.plan.version})\n{marks}")

    parts.append(f"# Your notes\n{state.notes.render()}")
    if state.writes_made:
        sent = "\n".join(f"- step {w['step']}: {w['method']} {w['path']} ({w['action']}) {w['fields']}" for w in state.writes_made)
        parts.append("# Writes you have sent in this run (recorded by the system, not by you)\n" + sent +
                     "\nThe system let these through; check that they took effect. If you later find these records, "
                     "they are the result of YOUR writes, not proof that the work was already there before you started.")
    verification = _verification(state)
    if verification:
        parts.append(f"# Verification so far\n{verification}")
    parts.append(f"# Budget\n{budget_line}")
    if state.forced_reason:
        parts.append(f"# NOTICE\n{state.forced_reason}")

    older = state.steps[:-VERBATIM_STEPS]
    if older:
        parts.append("# Earlier steps (one line each)\n" + "\n".join(
            f"step {s.n}: {s.summary}" + (" [error]" if s.error else "") for s in older))

    recent = state.steps[-VERBATIM_STEPS:]
    if recent:
        blocks = []
        for s in recent:
            tag = " (latest)" if s is recent[-1] else ""
            blocks.append(f"## Step {s.n}{tag}\nreason: {s.reason or '(none)'}\ncall: {_call_line(s)}\nresult:\n{s.result_text}")
        parts.append("# Most recent steps (newest last)\n" + "\n\n".join(blocks))
    else:
        parts.append("# Most recent steps\n(none yet: this is your first step)")

    parts.append("Call exactly one tool now.")
    return "\n\n".join(parts)
