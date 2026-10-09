"""SOP loading, the LLM wrapper, structured calls, and how the state becomes context."""
import json
import types

import pytest

from agent.contract import TaskContract
from agent.llm import LiteLLMClient, LLMError, LLMResponse, ToolCall, Usage, settings_from_env, structured_call
from agent.notes import Notes
from agent.pack import PackError, load_sops, merge_requirements, parse_sop
from agent.state import RunState, StepRecord, render_state
from tests.agent.fakes import FakeLLM, call, response
from tests.agent.helpers import PACK


# ---- the handbook -----------------------------------------------------------------

def test_the_four_company_sops_load():
    sops = {s.id: s for s in load_sops(PACK / "sops")}
    assert set(sops) == {"vendor_invoice_entry", "invoice_revision_update", "vendor_onboarding", "mark_bill_paid"}
    assert sops["vendor_invoice_entry"].done_requires == {"erp_bills": ["vendor_id", "invoice_no", "amount", "due_date"]}
    assert sops["mark_bill_paid"].allow_literal == {"erp_bills": ["status"]}
    assert "MOST RECENT" in sops["vendor_invoice_entry"].body and "Done means" in sops["vendor_invoice_entry"].body
    assert sops["vendor_onboarding"].catalog_line().startswith("- vendor_onboarding: Onboarding a new vendor from a form.")


def test_sops_state_rules_not_click_paths():
    """The handbook must not tell the agent which buttons to press."""
    for sop in load_sops(PACK / "sops"):
        for word in ("click", "button", "press", "navigate to", "URL"):
            assert word.lower() not in sop.body.lower(), (sop.id, word)


def test_requirements_are_merged_across_selected_sops():
    sops = {s.id: s for s in load_sops(PACK / "sops")}
    done, literal = merge_requirements([sops["vendor_invoice_entry"], sops["mark_bill_paid"]])
    assert done == {"erp_bills": ["vendor_id", "invoice_no", "amount", "due_date", "status"]}
    assert literal == {"erp_bills": ["status"]}
    assert merge_requirements([]) == ({}, {})


@pytest.mark.parametrize("text, fragment", [
    ("no front matter", "must start with"),
    ("---\nid: x\ntitle: t\nsummary: s\n", "not closed"),
    ("---\ntitle: t\nsummary: s\n---\nbody", "id: Field required"),
    ("---\nid: x\ntitle: t\nsummary: s\nsurprise: 1\n---\nbody", "surprise: Extra inputs"),
    ("---\n: bad: yaml: [\n---\nbody", "x.md: front matter is not valid"),
])
def test_bad_sops_are_rejected(text, fragment):
    with pytest.raises(PackError, match=fragment):
        parse_sop(text, "x.md")


def test_duplicate_sop_ids_are_rejected(tmp_path):
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_text("---\nid: same\ntitle: t\nsummary: s\n---\nbody", encoding="utf-8")
    with pytest.raises(PackError, match="duplicate SOP id"):
        load_sops(tmp_path)


# ---- the LiteLLM wrapper (litellm itself is replaced by a fake) ---------------------------

class FakeLitellm:
    def __init__(self, tool_calls=(), content="", usage=(120, 30), cost=0.002, error=None):
        self.kwargs = None
        self._tool_calls, self._content, self._usage, self._cost, self._error = tool_calls, content, usage, cost, error

    def completion(self, **kwargs):
        self.kwargs = kwargs
        if self._error:
            raise self._error
        calls = [types.SimpleNamespace(function=types.SimpleNamespace(name=n, arguments=a)) for n, a in self._tool_calls]
        message = types.SimpleNamespace(content=self._content, tool_calls=calls)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)],
                                     usage=types.SimpleNamespace(prompt_tokens=self._usage[0], completion_tokens=self._usage[1]))

    def completion_cost(self, completion_response):
        if self._cost is None:
            raise ValueError("model not in the price table")
        return self._cost


def client_with(fake, **kwargs):
    client = LiteLLMClient("gemini/some-model", **kwargs)
    client._litellm = fake
    return client


def test_a_tool_call_is_parsed_with_usage_and_cost():
    fake = FakeLitellm([("note", '{"key": "a", "value": "b", "reason": "r"}')])
    out = client_with(fake).complete("SYS", "USER", [{"type": "function"}])
    assert out.tool_calls == [ToolCall("note", {"key": "a", "value": "b", "reason": "r"}, None, '{"key": "a", "value": "b", "reason": "r"}')]
    assert (out.usage.prompt_tokens, out.usage.completion_tokens, out.usage.cost_usd) == (120, 30, 0.002)
    assert fake.kwargs["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert fake.kwargs["parallel_tool_calls"] is False and "tool_choice" not in fake.kwargs  # never forced
    assert "temperature" not in fake.kwargs  # only sent when configured


def test_temperature_is_sent_only_when_configured():
    fake = FakeLitellm()
    client_with(fake, temperature=0.0).complete("s", "u", [])
    assert fake.kwargs["temperature"] == 0.0


def test_unknown_price_means_cost_none_not_a_guess():
    out = client_with(FakeLitellm(cost=None)).complete("s", "u", [])
    assert out.usage.cost_usd is None and out.usage.prompt_tokens == 120


def test_broken_tool_arguments_are_flagged_not_raised():
    out = client_with(FakeLitellm([("note", "{oops"), ("note", "[1]"), ("note", "")])).complete("s", "u", [])
    assert [c.error for c in out.tool_calls] == ["arguments are not valid JSON", "arguments are not a JSON object", None]
    assert out.tool_calls[2].arguments == {}


def test_provider_errors_become_llm_errors_without_request_details():
    with pytest.raises(LLMError, match="RuntimeError while calling gemini/some-model: boom"):
        client_with(FakeLitellm(error=RuntimeError("boom\nsk-secret-should-not-appear-on-line-2"))).complete("s", "u", [])


class RateLimitError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class Flaky(FakeLitellm):
    """Fails with the given errors first, then answers."""

    def __init__(self, errors, **kwargs):
        super().__init__(**kwargs)
        self.errors, self.attempts = list(errors), 0

    def completion(self, **kwargs):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        return super().completion(**kwargs)


def test_rate_limits_are_retried_with_a_longer_backoff_and_then_succeed():
    sleeps = []
    fake = Flaky([RateLimitError("slow down"), RateLimitError("slow down")])
    out = client_with(fake, sleep=sleeps.append).complete("s", "u", [])
    assert fake.attempts == 3 and sleeps == [5, 10] and out.usage.prompt_tokens == 120  # free tiers limit per MINUTE


class ServiceUnavailableError(Exception):
    pass


def test_an_overloaded_provider_gets_the_same_longer_backoff_as_a_rate_limit():
    sleeps = []
    fake = Flaky([ServiceUnavailableError("The model is overloaded. Please try again later."), ServiceUnavailableError("busy")])
    out = client_with(fake, sleep=sleeps.append).complete("s", "u", [])
    assert sleeps == [5, 10] and fake.attempts == 3 and out.usage.prompt_tokens == 120


def test_the_wait_the_provider_asks_for_is_honoured():
    sleeps = []
    fake = Flaky([RateLimitError('Quota exceeded. Please retry in 21.5s. {"retryDelay": "21s"}')])
    client_with(fake, sleep=sleeps.append).complete("s", "u", [])
    assert sleeps == [22.5]


def test_a_daily_quota_is_not_retried_and_the_message_says_what_to_do():
    sleeps = []
    fake = Flaky([RateLimitError("429 ... quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier ...")])
    with pytest.raises(LLMError, match="daily or monthly request quota .* used up; retrying will not help"):
        client_with(fake, sleep=sleeps.append).complete("s", "u", [])
    assert fake.attempts == 1 and sleeps == []


def test_a_monthly_quota_is_treated_like_a_daily_one():
    fake = Flaky([RateLimitError("Monthly token limit exceeded for this plan")])
    with pytest.raises(LLMError, match="retrying will not help"):
        client_with(fake, sleep=lambda s: pytest.fail("must not sleep")).complete("s", "u", [])
    assert fake.attempts == 1


def test_an_absurdly_long_requested_pause_is_refused_instead_of_slept():
    fake = Flaky([RateLimitError("Please retry in 3600s")])
    with pytest.raises(LLMError, match="asks for a 3601 s pause, longer than the 120 s"):
        client_with(fake, sleep=lambda s: pytest.fail("must not sleep")).complete("s", "u", [])


def test_calls_are_spaced_out_by_the_minimum_interval():
    now, sleeps = [100.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    client = client_with(FakeLitellm(), min_interval=6.0, sleep=sleep, clock=lambda: now[0])
    client.complete("s", "u", [])      # the first call is never delayed
    now[0] += 2.0                      # the model call and the tool took 2 s
    client.complete("s", "u", [])      # so 4 more seconds are needed to reach 6
    now[0] += 10.0
    client.complete("s", "u", [])      # already more than 6 s: no wait
    assert sleeps == [4.0]


def test_reasoning_effort_is_passed_on_only_when_set():
    fake = FakeLitellm()
    client_with(fake).complete("s", "u", [])
    assert "reasoning_effort" not in fake.kwargs
    client_with(fake, reasoning_effort="low").complete("s", "u", [])
    assert fake.kwargs["reasoning_effort"] == "low"


@pytest.mark.parametrize("model, env", [
    ("gemini/gemini-3.8-flash", "GEMINI_API_KEY"), ("mistral/mistral-medium-latest", "MISTRAL_API_KEY"),
    ("nvidia_nim/some-model", "NVIDIA_NIM_API_KEY"), ("openai/gpt-x", "OPENAI_API_KEY"), ("ollama/llama3", None),
])
def test_the_key_a_model_needs_is_known_and_only_its_presence_is_checked(monkeypatch, model, env):
    from agent.llm import key_problem, required_key_env
    assert required_key_env(model) == env
    if env:
        monkeypatch.setenv(env, "")
        assert key_problem(model) == f"{env} is not set (put it in .env); the model {model} needs it"
        monkeypatch.setenv(env, "not-a-real-key")
        assert key_problem(model) is None
    else:
        assert key_problem(model) is None  # local or unknown providers need no key here


def test_errors_that_will_not_get_better_are_not_retried():
    sleeps = []
    fake = Flaky([AuthenticationError("bad key")])
    with pytest.raises(LLMError, match="AuthenticationError while calling"):
        client_with(fake, sleep=sleeps.append).complete("s", "u", [])
    assert fake.attempts == 1 and sleeps == []


def test_retries_stop_after_the_configured_number_and_status_codes_count():
    class Server500(Exception):
        status_code = 503

    fake = Flaky([Server500("down")] * 5)
    with pytest.raises(LLMError, match="Server500"):
        client_with(fake, retries=2, sleep=lambda s: None).complete("s", "u", [])
    assert fake.attempts == 3


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    with pytest.raises(LLMError, match="AGENT_MODEL is not set"):
        settings_from_env()
    for name in ("AGENT_TEMPERATURE", "AGENT_MAX_TOKENS", "AGENT_MAX_RETRIES", "AGENT_MIN_INTERVAL_SECONDS", "AGENT_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_MODEL", "gemini/x")
    monkeypatch.setenv("AGENT_TEMPERATURE", "")
    assert settings_from_env() == {"model": "gemini/x", "temperature": None, "max_tokens": 8192, "retries": 3,
                                   "min_interval": 0.0, "reasoning_effort": None}
    monkeypatch.setenv("AGENT_TEMPERATURE", "0")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "1000")
    monkeypatch.setenv("AGENT_MAX_RETRIES", "5")
    monkeypatch.setenv("AGENT_MIN_INTERVAL_SECONDS", "6.5")
    monkeypatch.setenv("AGENT_REASONING_EFFORT", "low")
    assert settings_from_env() == {"model": "gemini/x", "temperature": 0.0, "max_tokens": 1000, "retries": 5,
                                   "min_interval": 6.5, "reasoning_effort": "low"}


def test_usage_adds_up_and_unknown_cost_is_sticky():
    total = Usage()
    total.add(Usage(10, 5, 0.5))
    total.add(Usage(1, 1, 0.25))
    assert (total.prompt_tokens, total.completion_tokens, total.cost_usd) == (11, 6, 0.75)
    total.add(Usage(1, 1, None))
    total.add(Usage(1, 1, 1.0))
    assert total.cost_usd is None


# ---- structured calls ----------------------------------------------------------------

def sink():
    seen = []
    return seen, lambda response, stage: seen.append(stage)


def test_structured_call_returns_a_validated_object():
    seen, record = sink()
    llm = FakeLLM([call("submit_contract", None, goal="g", ambiguities=["q?"])])
    contract = structured_call(llm, "sys", "user", "submit_contract", "d", TaskContract, record)
    assert contract.goal == "g" and contract.ambiguities == ["q?"] and seen == ["submit_contract"]
    assert llm.calls[0]["tools"][0]["function"]["name"] == "submit_contract"
    assert "title" not in json.dumps(llm.calls[0]["tools"])
    assert "calling the tool `submit_contract` exactly once" in llm.calls[0]["user"]


def test_structured_call_retries_with_the_reason_and_gives_up_loudly():
    seen, record = sink()
    llm = FakeLLM([response(text="I will think"), call("submit_contract", None), call("submit_contract", None, goal="ok")])
    assert structured_call(llm, "s", "u", "submit_contract", "d", TaskContract, record).goal == "ok"
    assert "you did not call `submit_contract`" in llm.calls[1]["user"]
    assert "goal: Field required" in llm.calls[2]["user"] and len(seen) == 3

    llm2 = FakeLLM([call("submit_contract", None)] * 3)
    with pytest.raises(LLMError, match="after 3 attempts"):
        structured_call(llm2, "s", "u", "submit_contract", "d", TaskContract, record)


def test_structured_call_check_function_can_reject():
    def check(answer):
        if answer.goal != "right":
            raise ValueError("the goal must be 'right'")

    llm = FakeLLM([call("submit_contract", None, goal="wrong"), call("submit_contract", None, goal="right")])
    assert structured_call(llm, "s", "u", "submit_contract", "d", TaskContract, lambda r, s: None, check=check).goal == "right"
    assert "the goal must be 'right'" in llm.calls[1]["user"]


# ---- state, not transcript --------------------------------------------------------------

def step(n, result="R" * 50, tool="browser_click", error=False):
    return StepRecord(n, "tool", tool, {"ref": n}, f"reason {n}", f"result-{n} {result}", f"summary of step {n}", error=error)


def state_with(steps):
    from agent.contract import TaskContract
    from agent.pack import Sop

    s = RunState(request="Do the thing", systems_text="SYSTEMS TEXT", notes=Notes())
    s.contract = TaskContract(goal="the goal", entities=["E1"], risk_notes=["R1"])
    s.sops = [Sop(id="rule_a", title="Rule A", summary="s", body="Rule body text")]
    s.notes.set("latest", "INV-2041")
    s.steps = steps
    return s


def test_the_state_message_has_every_section_in_a_fixed_order():
    text = render_state(state_with([]), "step 1 of 40")
    positions = [text.index(h) for h in ("# Request", "# Your understanding", "# Systems you can use", "# Company rules that apply",
                                         "# Your notes", "# Budget", "# Most recent steps")]
    assert positions == sorted(positions)
    assert "Do the thing" in text and "Goal: the goal" in text and "SYSTEMS TEXT" in text and "## Rule A (id: rule_a)" in text
    assert "- latest: INV-2041" in text and "this is your first step" in text and text.endswith("Call exactly one tool now.")


def test_only_the_last_three_steps_are_verbatim_and_older_ones_are_one_line():
    text = render_state(state_with([step(n) for n in range(1, 11)]), "b")
    for n in (8, 9, 10):
        assert f"result-{n} " in text and f"## Step {n}" in text
    for n in range(1, 8):
        assert f"result-{n} " not in text and f"step {n}: summary of step {n}" in text
    assert "## Step 10 (latest)" in text and "## Step 9 (latest)" not in text


def test_the_context_does_not_grow_with_the_number_of_steps():
    short = len(render_state(state_with([step(n) for n in range(1, 6)]), "b"))
    long = len(render_state(state_with([step(n) for n in range(1, 41)]), "b"))
    assert long - short < 40 * 60  # only one summary line per extra step, never the full results


def test_clarification_and_forced_notices_appear_in_the_state():
    s = state_with([])
    s.contract.ambiguities = ["Which one?"]
    s.needs_clarification = True
    s.forced_reason = "The retry budget for 'noop' problems is used up."
    text = render_state(s, "b")
    assert "Open questions for the requester: Which one?" in text and "YOU MUST FIRST ask the requester" in text
    assert "# NOTICE\nThe retry budget for 'noop' problems is used up." in text


def test_there_is_no_verification_section_before_anything_was_checked():
    assert "# Verification so far" not in render_state(state_with([step(n) for n in range(1, 6)]), "b")


def test_accepted_assertions_stay_visible_after_their_step_scrolled_out():
    """Seen in a real run: the acceptance message left the 3-step window and the model declared again, nine times."""
    s = state_with([step(n) for n in range(1, 12)])
    s.self_checks.append({"step": 4, "passed": True, "detail": "d"})
    s.assertion_versions.append({"step": 5, "yaml": "assertions:\n  - kind: row_exists\n    table: t"})
    text = render_state(s, "b")
    assert "# Verification so far" in text and "self-check (step 4): matched the source" in text
    assert "assertions ACCEPTED at step 5 (version 1)" in text and "  - kind: row_exists" in text
    assert "call finish" in text and "Declare again only if you must change them" in text
    assert text.index("# Verification so far") < text.index("# Budget")


def test_a_failed_self_check_and_the_audit_feedback_stay_visible():
    s = state_with([step(1)])
    s.self_checks.append({"step": 1, "passed": False, "detail": "d"})
    s.audit_feedback = "check 'x' failed: the amount differs"
    text = render_state(s, "b")
    assert "DID NOT MATCH" in text and "the independent audit REJECTED your last completion claim" in text
    assert "check 'x' failed: the amount differs" in text


def test_errors_in_old_steps_are_marked_and_plan_progress_is_shown():
    from agent.state import PlanItem, PlanVersion

    s = state_with([step(1, error=True), step(2), step(3), step(4), step(5)])
    s.plans.append(PlanVersion(2, [PlanItem("read", True), PlanItem("write")], "progress", 3))
    text = render_state(s, "b")
    assert "step 1: summary of step 1 [error]" in text and "step 2: summary of step 2\n" in text
    assert "# Plan (version 2)\n[x] read\n[ ] write" in text
