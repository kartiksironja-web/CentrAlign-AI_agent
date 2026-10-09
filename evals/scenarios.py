"""Loading and validating evals/scenarios.yaml.

Purpose: turn the scenario file into typed objects before any run starts.
Why this design: strict models (unknown keys, duplicate ids, unknown seed variants are errors)
so a typo in a scenario cannot silently change what is being measured. The file is DATA: no
scenario knowledge lives in the harness code.
How it can fail: `seed_variant` only accepts "default" until a later phase adds variants;
a scenario that expects an outcome the loop never produces is simply never met.
"""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Scalar = str | int | float | None
KNOWN_SEED_VARIANTS = ("default",)
OUTCOMES = ("verified_done", "verified_no_action", "needs_human", "blocked", "audit_failed",
            "false_completion", "budget_exhausted", "error")


class ScenarioError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScriptedHumanSpec(_Strict):
    """SCRIPTED_HUMAN: pre-written answers, used in order per kind."""
    clarification: list[str] = Field(default_factory=list)
    approval: list[str] = Field(default_factory=list)


class RowExpectation(_Strict):
    table: str
    where: dict[str, Scalar]
    count: int


class GroundTruth(_Strict):
    row_delta: dict[str, int] = Field(default_factory=dict)
    changed_tables: list[str] = Field(default_factory=list)
    rows: list[RowExpectation] = Field(default_factory=list)


class Expect(_Strict):
    outcomes: list[str]
    ground_truth: GroundTruth
    min_humans: int = 0
    max_humans: int | None = None
    min_gate_blocks: int = 0
    no_write_before_human: bool = False

    @model_validator(mode="after")
    def _known_outcomes(self):
        unknown = [o for o in self.outcomes if o not in OUTCOMES]
        if unknown:
            raise ValueError(f"unknown outcome(s) {unknown}; valid: {list(OUTCOMES)}")
        return self


class Scenario(_Strict):
    id: str
    priority: Literal["P0", "P1", "P2"]
    name: str
    task: str
    expect: Expect
    faults: list[str] = Field(default_factory=list)
    seed_variant: str = "default"
    audit_mode: Literal["enforce", "advisory"] = "enforce"
    scripted_human: ScriptedHumanSpec = Field(default_factory=ScriptedHumanSpec)

    @model_validator(mode="after")
    def _known_variant(self):
        if self.seed_variant not in KNOWN_SEED_VARIANTS:
            raise ValueError(f"unknown seed_variant {self.seed_variant!r}; known: {list(KNOWN_SEED_VARIANTS)}")
        return self


def load_scenarios(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    scenarios = []
    for i, item in enumerate(raw.get("scenarios") or [], start=1):
        try:
            scenarios.append(Scenario.model_validate({**defaults, **item}))
        except ValidationError as e:
            name = item.get("id", f"#{i}") if isinstance(item, dict) else f"#{i}"
            problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            raise ScenarioError(f"scenario {name}: {problems}")
    ids = [s.id for s in scenarios]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ScenarioError(f"duplicate scenario id(s): {dupes}")
    if not scenarios:
        raise ScenarioError("no scenarios found")
    return scenarios


def select(scenarios: list[Scenario], wanted: list[str] | None) -> list[Scenario]:
    if not wanted:
        return scenarios
    by_id = {s.id: s for s in scenarios}
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        raise ScenarioError(f"unknown scenario id(s) {unknown}; known: {list(by_id)}")
    return [by_id[w] for w in wanted]
