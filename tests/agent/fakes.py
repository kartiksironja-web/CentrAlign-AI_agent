"""A FakeLLM that replays scripted tool calls, so the loop itself is tested deterministically.

The script is a list consumed in order. The first three entries answer the set-up calls
(contract, rule selection, plan); every later entry answers one step. An entry is an
LLMResponse, or a function (system, user, tools) -> LLMResponse that can look at the state
message the model would have seen and decide what to call (for example to read a ref).
"""
import re

from agent.llm import LLMResponse, ToolCall, Usage


class FakeLLM:
    model = "fake/scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def complete(self, system, user, tools):
        self.calls.append({"system": system, "user": user, "tools": tools})
        if not self.script:
            raise AssertionError(f"the FakeLLM script ran out (call number {len(self.calls)})")
        item = self.script.pop(0)
        return item(system, user, tools) if callable(item) else item


def response(*tool_calls: ToolCall, text: str = "", cost: float | None = 0.001) -> LLMResponse:
    return LLMResponse(text, list(tool_calls), Usage(1000, 100, cost), 5)


def call(name: str, why: str | None = "because", *, cost: float | None = 0.001, **args) -> LLMResponse:
    """One tool call with a reason (pass why=None for set-up answers, which have no reason)."""
    return response(tool_call(name, why, **args), cost=cost)


def tool_call(name: str, why: str | None = "because", **args) -> ToolCall:
    return ToolCall(name, {**({"reason": why} if why is not None else {}), **args})


def text(message: str) -> LLMResponse:
    return response(text=message)


def prep(*sop_ids: str, ambiguities=(), plan=("read the sources", "do the work", "verify it")):
    """The three set-up answers."""
    return [
        call("submit_contract", None, goal="do the request", ambiguities=list(ambiguities)),
        call("select_rules", None, sop_ids=list(sop_ids), reason="these apply"),
        call("submit_plan", None, items=list(plan)),
    ]


def ref_in(user: str, kind: str, label: str) -> int:
    """Find an element number in the latest snapshot inside the state message, like the model would."""
    matches = re.findall(rf'\[(\d+)\] {re.escape(kind)} "{re.escape(label)}', user)
    assert matches, f"no {kind} {label!r} in the state:\n{user[-2500:]}"
    return int(matches[-1])


def browser_step(name: str, kind: str, label: str, why: str = "because", **extra):
    """A step that acts on an element found by what it says in the latest snapshot."""
    return lambda system, user, tools: call(name, why, ref=ref_in(user, kind, label), **extra)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds
