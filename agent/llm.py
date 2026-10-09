"""The only module that talks to a language model (through LiteLLM).

Purpose: `LLM.complete(system, user, tools)` returns text and/or tool calls plus token usage
and cost; `structured_call` gets a validated Pydantic object out of a model.
Why this design:
  * One narrow interface. LiteLLM is imported only here, so swapping it for another client
    (or for the scripted FakeLLM in tests) touches nothing else. The provider is chosen by the
    model string alone (`gemini/...`, `openai/...`, ...) plus its key in .env.
  * No forced tool choice and no assumption about sampling parameters: newer models reject
    both. Tools are offered with tool_choice left on auto, and the LOOP enforces "exactly
    one tool call" in code. Temperature is sent only if AGENT_TEMPERATURE is set (Gemini 3
    models are best left at their default).
  * Structured output is done with a tool call (one tool whose schema is the Pydantic
    model), which every tool-calling model supports, then validated with Pydantic and
    retried with the error message if invalid.
  * Cost comes from LiteLLM's price table (list prices). On a free tier nothing is charged;
    if the model is not in the table, cost is None and only tokens are reported.
  * Free tiers have per-minute and per-day request limits. `min_interval` spaces calls out;
    a rate-limit error is retried after the wait the provider asks for; a DAILY quota error
    is not retried (waiting would not help) and says so.
How it can fail: provider errors become LLMError with the key never included; a model that
answers in prose instead of calling a tool gets two more attempts in `structured_call`.
"""
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from pydantic import BaseModel, ValidationError

from agent.toolbox import clean_schema

MAX_WAIT_SECONDS = 120  # if a provider asks for a longer pause than this, give up instead of sleeping
PROVIDER_KEYS = {"gemini/": "GEMINI_API_KEY", "mistral/": "MISTRAL_API_KEY", "nvidia_nim/": "NVIDIA_NIM_API_KEY",
                 "openai/": "OPENAI_API_KEY"}
RETRYABLE = {"RateLimitError", "APIConnectionError", "Timeout", "ServiceUnavailableError", "InternalServerError"}


class LLMError(Exception):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = 0.0  # None once any call had an unknown price

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd = None if self.cost_usd is None or other.cost_usd is None else self.cost_usd + other.cost_usd


@dataclass
class ToolCall:
    name: str
    arguments: dict
    error: str | None = None  # set when the arguments were not valid JSON
    raw: str = ""


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    latency_ms: int = 0


class LLM(Protocol):
    model: str

    def complete(self, system: str, user: str, tools: list[dict]) -> LLMResponse: ...


def required_key_env(model: str) -> str | None:
    """Which environment variable holds the key for this model's provider (None if unknown)."""
    return next((env for prefix, env in PROVIDER_KEYS.items() if model.startswith(prefix)), None)


def key_problem(model: str) -> str | None:
    """A message if the provider's key is missing, else None. Only checks presence, never prints the key."""
    env = required_key_env(model)
    if env and not os.environ.get(env, "").strip():
        return f"{env} is not set (put it in .env); the model {model} needs it"
    return None


def settings_from_env() -> dict:
    model = os.environ.get("AGENT_MODEL", "").strip()
    if not model:
        raise LLMError("AGENT_MODEL is not set; copy .env.example to .env and set it")
    temperature = os.environ.get("AGENT_TEMPERATURE", "").strip()
    return {
        "model": model,
        "temperature": float(temperature) if temperature else None,
        "max_tokens": int(os.environ.get("AGENT_MAX_TOKENS") or 8192),
        "retries": int(os.environ.get("AGENT_MAX_RETRIES") or 3),
        "min_interval": float(os.environ.get("AGENT_MIN_INTERVAL_SECONDS") or 0),
        "reasoning_effort": os.environ.get("AGENT_REASONING_EFFORT", "").strip() or None,
    }


def is_retryable(error: Exception) -> bool:
    """Rate limits, timeouts, connection problems and server errors are worth another try;
    a bad key, a bad request or an unknown model are not."""
    status = getattr(error, "status_code", None)
    return type(error).__name__ in RETRYABLE or status in (408, 409, 429) or (isinstance(status, int) and status >= 500)


def is_rate_limit(error: Exception) -> bool:
    return type(error).__name__ == "RateLimitError" or getattr(error, "status_code", None) == 429


def is_overloaded(error: Exception) -> bool:
    """The provider is busy (HTTP 503): like a rate limit, a short pause is not enough."""
    return type(error).__name__ == "ServiceUnavailableError" or getattr(error, "status_code", None) == 503


def is_daily_quota(error: Exception) -> bool:
    """A quota that resets only after hours or days (daily or monthly): waiting a few seconds cannot help."""
    text = str(error)
    lowered = text.lower()
    return "PerDay" in text or "per day" in lowered or "daily" in lowered or "monthly" in lowered or "per month" in lowered


def retry_delay_from(error: Exception) -> float | None:
    """Seconds the provider says to wait ("Please retry in 21.5s" / "retryDelay": "21s"), if it says."""
    match = re.search(r'(?:retryDelay"?\s*[:=]\s*"?|retry in\s*)(\d+(?:\.\d+)?)\s*s', str(error), re.I)
    return float(match.group(1)) if match else None


class LiteLLMClient:
    def __init__(self, model: str, temperature: float | None = None, max_tokens: int = 8192,
                 timeout: float = 90.0, retries: int = 3, min_interval: float = 0.0,
                 reasoning_effort: str | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        import litellm  # heavy import, kept out of module import time

        litellm.suppress_debug_info = True  # its error banner is noise in our reports
        self._litellm = litellm
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens
        self.timeout, self.retries, self.min_interval = timeout, retries, min_interval
        self.reasoning_effort = reasoning_effort
        self._sleep, self._clock, self._last_call = sleep, clock, None

    def _throttle(self) -> None:
        """Keep at least `min_interval` seconds between calls (free tiers limit requests per minute)."""
        if self.min_interval and self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)

    def _wait_for(self, error: Exception, attempt: int) -> float:
        delay = retry_delay_from(error)
        if delay is not None:
            return delay + 1  # a second of margin
        return min((5 if is_rate_limit(error) or is_overloaded(error) else 1) * 2 ** attempt, 60)

    def complete(self, system: str, user: str, tools: list[dict]) -> LLMResponse:
        # Our own small retry loop rather than LiteLLM's num_retries, which needs an optional
        # package (tenacity) and hides what it is doing.
        kwargs = dict(
            model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=tools, max_tokens=self.max_tokens, timeout=self.timeout,
            parallel_tool_calls=False, drop_params=True,
        )
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                response = self._litellm.completion(**kwargs)
                self._last_call = self._clock()
                break
            except Exception as e:
                self._last_call = self._clock()
                first_line = str(e).splitlines()[0][:200] if str(e) else ""
                if is_rate_limit(e) and is_daily_quota(e):
                    raise LLMError(f"the daily or monthly request quota for {self.model} is used up; retrying will not "
                                   f"help. Try later or another model. Provider said: {first_line}")
                if attempt < self.retries and is_retryable(e):
                    wait = self._wait_for(e, attempt)
                    if wait > MAX_WAIT_SECONDS:
                        raise LLMError(f"rate limited: the provider asks for a {wait:.0f} s pause, longer than the "
                                       f"{MAX_WAIT_SECONDS} s this client will wait ({first_line})")
                    self._sleep(wait)
                    continue
                # never include the exception text blindly: it can echo request details
                raise LLMError(f"{type(e).__name__} while calling {self.model}: {first_line}")
        message = response.choices[0].message
        calls = []
        for tc in getattr(message, "tool_calls", None) or []:
            name, raw = tc.function.name, tc.function.arguments or "{}"
            try:
                parsed = json.loads(raw)
            except ValueError:
                calls.append(ToolCall(name, {}, "arguments are not valid JSON", raw))
                continue
            if not isinstance(parsed, dict):
                calls.append(ToolCall(name, {}, "arguments are not a JSON object", raw))
                continue
            calls.append(ToolCall(name, parsed, None, raw))
        usage = response.usage
        try:
            cost = float(self._litellm.completion_cost(completion_response=response))
        except Exception:
            cost = None  # model not in LiteLLM's price table
        return LLMResponse(
            text=message.content or "", tool_calls=calls,
            usage=Usage(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0, cost),
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def tool_spec(name: str, description: str, schema_model: type[BaseModel]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": clean_schema(schema_model.model_json_schema()),
    }}


def structured_call(llm: LLM, system: str, user: str, tool_name: str, description: str,
                    model_cls: type[BaseModel], usage_sink: Callable[[LLMResponse, str], None],
                    check: Callable[[BaseModel], None] | None = None, attempts: int = 3) -> BaseModel:
    """Ask for an answer shaped like `model_cls` by offering one tool of that shape.

    `check` may raise ValueError with a message the model can act on. After `attempts`
    invalid answers an LLMError is raised.
    """
    tools = [tool_spec(tool_name, description, model_cls)]
    prompt = f"{user}\n\nAnswer by calling the tool `{tool_name}` exactly once."
    problem = ""
    for _ in range(attempts):
        response = llm.complete(system, prompt + (f"\n\nYour previous answer was rejected: {problem}" if problem else ""), tools)
        usage_sink(response, tool_name)
        calls = [c for c in response.tool_calls if c.name == tool_name]
        if not calls:
            problem = f"you did not call `{tool_name}`"
            continue
        if calls[0].error:
            problem = calls[0].error
            continue
        try:
            answer = model_cls.model_validate(calls[0].arguments)
            if check:
                check(answer)
            return answer
        except ValidationError as e:
            problem = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        except ValueError as e:
            problem = str(e)
    raise LLMError(f"the model did not produce a valid `{tool_name}` after {attempts} attempts ({problem})")
