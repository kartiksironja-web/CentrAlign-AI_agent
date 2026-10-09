"""The agent loop: Goal -> Understand -> Plan -> Execute -> Observe -> Adapt -> Verify -> Complete.

Purpose: `Agent.run(request)` prepares (contract, handbook selection, plan), then repeats
"build the state, ask the model for ONE tool call, run it, observe, run the detectors, log
the step" until the work is finished, handed over, or a budget runs out. When the model
claims completion, an independent audit decides whether that is true.
The LLM proposes, code disposes. The model decides what to do next. This loop (code) decides:
  * budgets: 60 steps, 600 s, a money cap; when one is used up the run ends, whatever the model thinks;
  * one tool call per step, with a reason; extra calls are ignored, missing reasons rejected;
  * that nothing but a question to the requester is allowed while the contract has open
    questions, and nothing but asking a human / finishing once a retry budget is used up;
  * whether `finish` is accepted: "done" needs declared assertions that pass validation AND an
    audit that passes (feedback ladder, 2 rounds, then the run is escalated to a human);
  * the outcome and the final report (see report.py), written from observed facts.
Audit modes: `enforce` (the audit result is fed back, retries allowed) and `advisory` (the audit
result is only recorded, so the agent's FIRST claim is final; used to measure false claims).
How it can fail: the model can stall or refuse the protocol (nudges and refusals are counted and
end the run as blocked); the model can write weak assertions (the validator enforces coverage of
what the company rules require, nothing more); every revision of the assertions is logged.
"""
import copy
import time
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent import prompts
from agent.audit_port import AuditPort
from agent.contract import PlanSubmission, SopSelection, TaskContract
from agent.detectors import Detectors
from agent.gate import Gate
from agent.human import is_approval
from agent.llm import LLM, LLMError, LLMResponse, ToolCall, structured_call, tool_spec
from agent.pack import Sop, merge_requirements
from agent.report import render_report
from agent.runlog import RunLog
from agent.snapshot import clip
from agent.state import PlanItem, PlanVersion, RunState, StepRecord, render_state
from agent.toolbox import Toolbox, ToolResult

REASON_HELP = "One short sentence: why you are making this call."
ALWAYS_ALLOWED_WHEN_FORCED = ("ask_human", "finish", "note", "self_check", "update_plan")


@dataclass
class RunConfig:
    max_steps: int = 60
    max_seconds: float = 600.0
    max_cost_usd: float | None = 1.0
    max_audit_rounds: int = 2  # feedback rounds; the next failure escalates to a human
    audit_mode: Literal["enforce", "advisory"] = "enforce"
    max_nudges: int = 3
    max_refusals: int = 3


@dataclass
class RunResult:
    run_id: str
    outcome: str
    report: str
    metrics: dict
    state: RunState
    log_path: str


# ---- control tools: the ones that act on the loop itself -------------------------------

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanItemArg(_Args):
    text: str
    done: bool = False


class UpdatePlanArgs(_Args):
    items: list[PlanItemArg] = Field(min_length=1, max_length=10, description="The whole checklist, with finished items marked done")
    change_reason: str = Field(description="Why the plan changes (progress, or something new you learned)")


class SelfCheckArgs(_Args):
    passed: bool = Field(description="Did the saved record match the source documents?")
    detail: str = Field(description="What you compared and what you found")


class DeclareArgs(_Args):
    yaml: str = Field(description="The assertions, as YAML")


class FinishArgs(_Args):
    status: Literal["done", "no_action_needed", "blocked", "needs_human"]
    summary: str = Field(description="What you did and found, for the requester")


CONTROL_TOOLS = {
    "update_plan": ("Replace your plan with a new version (finished items marked done) and say why.", UpdatePlanArgs),
    "self_check": ("Record whether you re-read the saved result and it matched the source documents.", SelfCheckArgs),
    "declare_assertions": ("Declare what must be true in the company systems. They are validated now and "
                           "checked by an independent audit when you finish.\n" + prompts.ASSERTION_FORMAT, DeclareArgs),
    "finish": ("End the work. done = verified complete; no_action_needed = already in place; blocked = cannot "
               "proceed; needs_human = a human must decide or act. done and no_action_needed need declared "
               "assertions and an audit that passes.", FinishArgs),
}


def with_reason(tool: dict) -> dict:
    """Every tool call must carry a one-sentence reason; add it to the schema, first and required."""
    tool = copy.deepcopy(tool)
    params = tool["function"]["parameters"]
    params["properties"] = {"reason": {"type": "string", "description": REASON_HELP}, **params.get("properties", {})}
    params["required"] = ["reason", *params.get("required", [])]
    return tool


class Agent:
    def __init__(self, *, llm: LLM, toolbox: Toolbox, gate: Gate, sops: list[Sop], systems_text: str,
                 audit: AuditPort, runlog: RunLog, config: RunConfig | None = None,
                 run_info: dict | None = None, clock: Callable[[], float] = time.monotonic,
                 on_event: Callable[[str, str], None] | None = None, detectors: Detectors | None = None):
        self.llm, self.toolbox, self.gate, self.sops = llm, toolbox, gate, sops
        self.systems_text, self.audit, self.runlog = systems_text, audit, runlog
        self.cfg = config or RunConfig()
        self.run_info = run_info or {}
        self.clock = clock
        self.emit = on_event or (lambda kind, text: None)
        self.detectors = detectors or Detectors()
        self.tools = [with_reason(t) for t in toolbox.openai_tools()] + [
            with_reason(tool_spec(name, description, model)) for name, (description, model) in CONTROL_TOOLS.items()
        ]
        self._started = 0.0

    # ---- bookkeeping ------------------------------------------------------------------------

    def _elapsed(self) -> float:
        return self.clock() - self._started

    def _usage(self, state: RunState, response: LLMResponse, stage: str) -> None:
        state.usage.add(response.usage)
        state.llm_calls.append({"stage": stage, "tokens_in": response.usage.prompt_tokens,
                                "tokens_out": response.usage.completion_tokens,
                                "cost_usd": response.usage.cost_usd, "ms": response.latency_ms})

    def _budget_line(self, state: RunState, n: int) -> str:
        left = max(0.0, self.cfg.max_seconds - self._elapsed())
        cost = state.usage.cost_usd
        spent = (f"spent about ${cost:.3f} of ${self.cfg.max_cost_usd:.2f}" if cost is not None and self.cfg.max_cost_usd
                 else f"{state.usage.prompt_tokens + state.usage.completion_tokens} tokens used")
        line = f"step {n} of {self.cfg.max_steps}; {left:.0f} s of {self.cfg.max_seconds:.0f} s left; {spent}."
        if self.cfg.max_steps - n < 5:
            line += " VERY FEW STEPS LEFT: finish, ask a human, or hand over now."
        return line

    def _budget_stop(self, state: RunState) -> str | None:
        if len(state.steps) >= self.cfg.max_steps:
            return f"all {self.cfg.max_steps} steps were used"
        if self._elapsed() >= self.cfg.max_seconds:
            return f"the {self.cfg.max_seconds:.0f} s time limit was reached"
        cost = state.usage.cost_usd
        if self.cfg.max_cost_usd is not None and cost is not None and cost >= self.cfg.max_cost_usd:
            return f"the cost limit of ${self.cfg.max_cost_usd:.2f} was reached"
        return None

    # ---- the run ---------------------------------------------------------------------------

    def run(self, request: str) -> RunResult:
        state = RunState(request=request, systems_text=self.systems_text, notes=self.toolbox.notes)
        self._started = self.clock()
        self.runlog.set("config", {**self.run_info, "max_steps": self.cfg.max_steps, "max_seconds": self.cfg.max_seconds,
                                   "max_cost_usd": self.cfg.max_cost_usd, "audit_mode": self.cfg.audit_mode,
                                   "detector_budget": self.detectors.budget, "model": self.llm.model})
        self.runlog.set("request", request)
        try:
            self._prepare(state)
            self._work(state)
        except LLMError as e:
            state.outcome, state.detail = "error", str(e)
        return self._conclude(state)

    def _prepare(self, state: RunState) -> None:
        catalog = "\n".join(s.catalog_line() for s in self.sops)
        sink = lambda response, stage: self._usage(state, response, stage)
        state.contract = structured_call(
            self.llm, prompts.UNDERSTAND_SYSTEM, prompts.understand_user(state.request, self.systems_text, catalog),
            "submit_contract", "Submit your contract for this request.", TaskContract, sink)
        state.needs_clarification = bool(state.contract.ambiguities)
        self.emit("contract", state.contract.render())

        if self.sops:
            by_id = {s.id: s for s in self.sops}

            def known(selection: SopSelection) -> None:
                bad = [i for i in selection.sop_ids if i not in by_id]
                if bad:
                    raise ValueError(f"unknown rule ids {bad}; valid ids: {sorted(by_id)}")

            selection = structured_call(
                self.llm, prompts.UNDERSTAND_SYSTEM, prompts.select_user(state.request, state.contract.render(), catalog),
                "select_rules", "Choose which company rules apply.", SopSelection, sink, check=known)
            state.sops = [by_id[i] for i in dict.fromkeys(selection.sop_ids)]
            self.emit("rules", ", ".join(s.id for s in state.sops) or "(none)")

        rules = "\n\n".join(f"{s.title}\n{s.body}" for s in state.sops)
        plan = structured_call(
            self.llm, prompts.UNDERSTAND_SYSTEM, prompts.plan_user(state.request, state.contract.render(), rules, self.systems_text),
            "submit_plan", "Submit your checklist plan.", PlanSubmission, sink)
        state.plans.append(PlanVersion(1, [PlanItem(t) for t in plan.items], "initial plan", 0))
        self.emit("plan", "\n".join(f"- {t}" for t in plan.items))
        self._write(state)

    def _work(self, state: RunState) -> None:
        while state.outcome is None:
            stop = self._budget_stop(state)
            if stop:
                state.outcome, state.detail = "budget_exhausted", stop
                return
            self._step(state)

    # ---- one step ----------------------------------------------------------------------------

    def _step(self, state: RunState) -> None:
        n = len(state.steps) + 1
        started = self.clock()
        response = self.llm.complete(prompts.SYSTEM_PROMPT, render_state(state, self._budget_line(state, n)), self.tools)
        self._usage(state, response, f"step {n}")
        if not response.tool_calls:
            record = self._nudge(state, n, response)
        else:
            record = self._execute(state, n, response.tool_calls[0], len(response.tool_calls) - 1)
        record.tokens_in, record.tokens_out = response.usage.prompt_tokens, response.usage.completion_tokens
        record.cost_usd, record.ms = response.usage.cost_usd, int((self.clock() - started) * 1000)
        state.steps.append(record)
        self.emit("step", f"{record.n} {record.tool} {record.reason}".strip())
        self._write(state)

    def _nudge(self, state: RunState, n: int, response: LLMResponse) -> StepRecord:
        state.nudges += 1
        text = "[SYSTEM] You answered without calling a tool. Call exactly one tool, with a one-sentence reason."
        if response.text:
            text += f" (You wrote: {clip(response.text, 200)!r})"
        if state.nudges > self.cfg.max_nudges:
            state.outcome, state.detail = "blocked", "the model would not follow the one-tool-call protocol"
        return StepRecord(n, "nudge", "(none)", {}, "", text, "no tool call")

    def _execute(self, state: RunState, n: int, call: ToolCall, extra_calls: int) -> StepRecord:
        reason = str(call.arguments.get("reason") or "").strip()
        args = {k: v for k, v in call.arguments.items() if k != "reason"}
        mark = len(self.gate.log)
        kind, detections = "tool", []
        if call.error:
            kind = "invalid"
            result = ToolResult(f"INVALID ARGUMENTS for {call.name}: {call.error}.", f"{call.name}: invalid arguments", error=True)
        elif not reason:
            kind = "invalid"
            result = ToolResult("INVALID: every call needs a one-sentence `reason`. Repeat the call with one.",
                                f"{call.name}: no reason", error=True)
        elif (why := self._refusal(state, call.name, args)):
            kind = "refused"
            state.refusals += 1
            result = ToolResult(why, f"{call.name}: refused")
            if state.refusals > self.cfg.max_refusals:
                state.outcome, state.detail = "blocked", "the model kept calling tools that are not allowed"
        elif call.name in CONTROL_TOOLS:
            kind = "control"
            result = self._control(state, n, call.name, args)
        else:
            result = self.toolbox.call(call.name, args)
            detections = self.detectors.inspect(call.name, args, result)

        writes = [e for e in self.gate.log[mark:] if e["effect"] == "allow" and e["method"] not in ("GET", "HEAD", "OPTIONS")]
        if kind == "tool":
            detections += self.detectors.inspect_writes([(e["method"], urlsplit(e["url"]).path, e.get("body_digest", "")) for e in writes])

        text = result.text
        for d in detections:
            text += "\n" + d.message
            if d.exhausted:
                state.forced_reason = (f"The retry budget for '{d.klass}' problems is used up. You may now only use "
                                       f"ask_human or finish (blocked or needs_human).")
        if result.human_unavailable:
            state.human_unavailable = True
            state.needs_clarification = False  # nobody can answer, so "ask first" would only make finish impossible
            state.forced_reason = "No human can answer. The only thing left is finish with status needs_human (or blocked)."
        if call.name == "ask_human" and kind == "tool" and not result.error and not result.human_unavailable \
                and args.get("kind") == "clarification":
            state.needs_clarification = False
        for entry in writes:
            state.writes_made.append({"step": n, "method": entry["method"], "path": urlsplit(entry["url"]).path,
                                      "action": entry.get("action") or "no named action", "fields": entry.get("fields") or {}})
        if extra_calls:
            text += f"\n[SYSTEM] You made {extra_calls + 1} tool calls in one step; only the first was run. One tool per step."
        return StepRecord(
            n, kind, call.name, args, reason, text, result.summary or call.name,
            gate=[dict(e) for e in self.gate.log[mark:]], detectors=[d.message for d in detections], error=result.error,
        )

    def _refusal(self, state: RunState, tool: str, args: dict) -> str | None:
        if state.human_unavailable and tool not in ("finish", "note", "self_check"):
            return f"Not allowed now. {state.forced_reason}"
        if state.forced_reason and tool not in ALWAYS_ALLOWED_WHEN_FORCED:
            return f"Not allowed now. {state.forced_reason}"
        if state.needs_clarification:
            if tool not in ("ask_human", "note", "update_plan"):
                return ("Not allowed yet. Your own analysis found open questions for the requester. Ask them with "
                        "ask_human(kind=clarification) first; nothing else is allowed until you have.")
            if tool == "ask_human" and args.get("kind") != "clarification":
                return "Ask the requester your open questions first, with ask_human(kind=clarification)."
        return None

    # ---- control tools -----------------------------------------------------------------------

    def _control(self, state: RunState, n: int, name: str, args: dict) -> ToolResult:
        try:
            parsed = CONTROL_TOOLS[name][1].model_validate(args)
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            return ToolResult(f"INVALID ARGUMENTS for {name}: {problems}", f"{name}: invalid arguments", error=True)
        return getattr(self, f"_do_{name}")(state, n, parsed)

    def _do_update_plan(self, state: RunState, n: int, a: UpdatePlanArgs) -> ToolResult:
        version = state.plan.version + 1 if state.plan else 1
        state.plans.append(PlanVersion(version, [PlanItem(i.text, i.done) for i in a.items], a.change_reason, n))
        return ToolResult(f"Plan updated (version {version}).", f"plan v{version}: {clip(a.change_reason, 60)}")

    def _do_self_check(self, state: RunState, n: int, a: SelfCheckArgs) -> ToolResult:
        state.self_checks.append({"step": n, "passed": a.passed, "detail": a.detail})
        text = "Self-check recorded." + ("" if a.passed else " It did not match: fix the problem before finishing.")
        return ToolResult(text, f"self-check {'passed' if a.passed else 'FAILED'}")

    def _requirements(self, state: RunState) -> tuple[dict, dict]:
        return merge_requirements(state.sops)

    def _do_declare_assertions(self, state: RunState, n: int, a: DeclareArgs) -> ToolResult:
        done_requires, allow_literal = self._requirements(state)
        outcome = self.audit.declare(a.yaml, done_requires, allow_literal)
        if outcome.ok:
            state.assertion_versions.append({"step": n, "yaml": a.yaml})
        return ToolResult(outcome.message, f"declare_assertions: {'accepted' if outcome.ok else 'rejected'}")

    def _do_finish(self, state: RunState, n: int, a: FinishArgs) -> ToolResult:
        if a.status not in ("done", "no_action_needed"):
            state.finish = {"step": n, "status": a.status, "summary": a.summary}
            state.outcome = a.status
            return ToolResult("Finished. The run is handed back.", f"finish: {a.status}")
        if state.assertions_yaml is None:
            return ToolResult("FINISH REJECTED: call declare_assertions first. A completion claim is only accepted "
                              "after you have declared what must be true.", "finish rejected: no assertions")
        done_requires, allow_literal = self._requirements(state)
        declared = self.audit.declare(state.assertions_yaml, done_requires, allow_literal)
        if not declared.ok:
            return ToolResult("FINISH REJECTED: " + declared.message, "finish rejected: assertions no longer valid")

        round_no = state.audit_failures + 1
        view = self.audit.audit(state.assertions_yaml, done_requires, allow_literal, round_no)
        state.audit_attempts.append({"step": n, "round": round_no, "passed": view.passed, "valid": view.valid,
                                     "detail": view.detail, "shown_to_agent": self.cfg.audit_mode == "enforce"})
        if not view.valid:
            return ToolResult("FINISH REJECTED: " + view.feedback, "finish rejected: assertions invalid")
        if state.first_claim is None:
            state.first_claim = {"step": n, "status": a.status, "audit_passed": view.passed}
        state.finish = {"step": n, "status": a.status, "summary": a.summary}
        verified = "verified_done" if a.status == "done" else "verified_no_action"

        if self.cfg.audit_mode == "advisory":
            state.outcome = verified if view.passed else "false_completion"
            return ToolResult("Finished.", f"finish: {a.status} (advisory audit {'passed' if view.passed else 'FAILED'})")
        if view.passed:
            state.outcome = verified
            return ToolResult("Finished. The independent audit passed.", f"finish: {a.status}, audit passed")
        state.audit_failures += 1
        if state.audit_failures <= self.cfg.max_audit_rounds:
            state.finish = None
            state.audit_feedback = view.feedback
            return ToolResult("FINISH REJECTED: the independent audit failed.\n" + view.feedback,
                              f"finish rejected: audit failed (round {round_no})")
        state.outcome, state.detail = "audit_failed", f"the audit failed {state.audit_failures} times"
        return ToolResult("The audit failed again. The run is handed to a human with the full details.",
                          "finish: audit failed, escalated")

    # ---- the end --------------------------------------------------------------------------------

    def _attention(self, state: RunState, audit: dict | None) -> list[str]:
        items = []
        if audit:
            items += [f"audit check '{c['id']}' failed: {c.get('detail') or 'expected ' + repr(c['expected']) + ', found ' + repr(c['actual'])}"
                      for c in audit["checks"] if not c["passed"]]
        if state.first_claim and not state.first_claim["audit_passed"]:
            items.append("the agent's FIRST claim of success was wrong; the audit caught it")
        for entry in self.gate.log:
            if entry["effect"] == "deny" and not entry.get("note"):
                items.append(f"policy blocked {entry['method']} {entry['url']} ({entry['rule_id']})")
        denied = [i for i in self.toolbox.interactions if i.kind == "approval" and not is_approval(i.reply)]
        if denied:
            items.append(f"{len(denied)} approval request(s) were not approved")
        exhausted = self.detectors.exhausted()
        if exhausted:
            items.append("retry budget used up for: " + ", ".join(exhausted))
        if state.human_unavailable:
            items.append("a human was needed but no answer was available")
        scripted = [i for i in self.toolbox.interactions if i.scripted]
        if scripted:
            items.append(f"{len(scripted)} human answer(s) came from a SCRIPTED human (an evaluation), not a person")
        if len(state.assertion_versions) > 1:
            items.append(f"the agent declared its assertions {len(state.assertion_versions)} times; review the revisions in the run log")
        if any(not c["passed"] for c in state.self_checks):
            items.append("a self-check reported a mismatch during the run")
        return items

    def _metrics(self, state: RunState) -> dict:
        usage = state.usage
        return {
            "steps": len(state.steps), "wall_seconds": self._elapsed(),
            "tokens_in": usage.prompt_tokens, "tokens_out": usage.completion_tokens, "cost_usd": usage.cost_usd,
            "detector_counts": dict(self.detectors.counts),
            "gate_blocks": sum(1 for e in self.gate.log if e["effect"] != "allow"),
            "human_interactions": len(self.toolbox.interactions),
            "audit_attempts": len(state.audit_attempts), "first_claim": state.first_claim,
            "self_checks": {"passed": sum(c["passed"] for c in state.self_checks),
                            "failed": sum(not c["passed"] for c in state.self_checks)},
            "assertion_versions": len(state.assertion_versions), "nudges": state.nudges, "refusals": state.refusals,
        }

    def _conclude(self, state: RunState) -> RunResult:
        state.outcome = state.outcome or "blocked"
        audit = state.audit_attempts[-1]["detail"] if state.audit_attempts else None
        if audit is None and state.assertions_yaml and state.outcome in ("needs_human", "blocked"):
            try:  # the agent handed over but had declared checks: run them for the record only
                done_requires, allow_literal = self._requirements(state)
                view = self.audit.audit(state.assertions_yaml, done_requires, allow_literal, 3)
                state.audit_attempts.append({"step": len(state.steps), "round": 0, "passed": view.passed, "valid": view.valid,
                                             "detail": view.detail, "shown_to_agent": False})
                audit = view.detail
            except Exception:
                audit = None
        changes = self.audit.changes()
        metrics = self._metrics(state)
        summary = state.finish["summary"] if state.finish else None
        report = render_report(
            run_id=self.runlog.run_id, request=state.request, outcome=state.outcome, detail=state.detail,
            agent_summary=summary, audit=audit, changes=changes, attention=self._attention(state, audit), metrics=metrics)
        log = self.runlog
        log.set("contract", state.contract.model_dump() if state.contract else None)
        log.set("sop_ids", [s.id for s in state.sops])
        log.set("plans", [{"version": p.version, "reason": p.reason, "step": p.step,
                           "items": [{"text": i.text, "done": i.done} for i in p.items]} for p in state.plans])
        log.set("self_checks", state.self_checks)
        log.set("assertions", state.assertion_versions)
        log.set("audit_attempts", state.audit_attempts)
        log.set("interactions", [vars(i) for i in self.toolbox.interactions])
        log.set("notes", state.notes.items())
        log.set("gate_log", self.gate.log)
        log.set("llm_calls", state.llm_calls)
        log.set("outcome", {"outcome": state.outcome, "detail": state.detail, "finish": state.finish})
        log.set("metrics", metrics)
        log.set("changes", changes)
        log.set("report", report)
        self._write(state)
        return RunResult(log.run_id, state.outcome, report, metrics, state, str(log.path))

    def _write(self, state: RunState) -> None:
        self.runlog.set("steps", [vars(s) for s in state.steps])
        self.runlog.set("plans", [{"version": p.version, "reason": p.reason, "step": p.step,
                                   "items": [{"text": i.text, "done": i.done} for i in p.items]} for p in state.plans])
        self.runlog.write()
