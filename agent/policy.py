"""The permission rules: loading policy.yaml and testing a rule against a request.

Purpose: turn the company's policy file into typed rules, and answer two questions
about one request: does a `match` rule apply (method / path pattern), and does an
action rule's condition hold for the fields read from the request.
Why this design: rules are data, not code, so permissions can change without
touching the runtime. Only two effects exist (deny, require_approval): "allow"
would be a no-op because everything not denied is already allowed.
Conditions FAIL CLOSED. If the value a rule needs is present but cannot be read
as a number ("nan", "abc", "80,000"), or is missing when the gate says it is required,
`evaluate_condition` raises Unreadable and the gate treats the rule as holding: the
request needs a human. Otherwise a malformed amount could slip past a "greater than"
rule, because nan > 50000 is False.
How it can fail: a rule can only test fields the connector extracts; a field the connector
lists as optional (one a request may legitimately leave out) is skipped when absent.
"""
import fnmatch
import math
import operator
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


class PolicyError(Exception):
    pass


class Unreadable(Exception):
    """A field a rule needs is present but cannot be compared as the rule requires."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Strict):
    field: str
    op: Literal[">", ">=", "<", "<=", "==", "!="]
    value: int | float | str


class Match(_Strict):
    method: str | None = None
    path_glob: str | None = None

    @model_validator(mode="after")
    def _not_empty(self):
        if self.method is None and self.path_glob is None:
            raise ValueError("match needs method and/or path_glob")
        return self


class Rule(_Strict):
    id: str
    effect: Literal["deny", "require_approval"]
    action: str | None = None
    when: Condition | None = None
    match: Match | None = None
    message: str = ""

    @model_validator(mode="after")
    def _shape(self):
        if (self.action is None) == (self.match is None):
            raise ValueError("a rule needs exactly one of 'action' or 'match'")
        if self.match is not None and (self.when is not None or self.effect != "deny"):
            raise ValueError("a 'match' rule must be effect: deny and cannot have 'when'")
        if self.action is None and self.when is not None:
            raise ValueError("'when' only works together with 'action'")
        return self


class Policy(_Strict):
    default_host_policy: Literal["deny"]
    rules: tuple[Rule, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [r.id for r in self.rules]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate rule id(s): {dupes}")
        return self


def parse_policy(data) -> Policy:
    try:
        return Policy.model_validate(data)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise PolicyError("invalid policy: " + " | ".join(lines))


def load_policy(path: Path) -> Policy:
    return parse_policy(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def match_rule_applies(rule: Rule, method: str, path: str) -> bool:
    """Does this `match` rule apply to the request? Case-insensitive on purpose: a deny
    rule that can be dodged by writing /ADMIN would be worthless."""
    m = rule.match
    if m.method is not None and m.method.upper() != method.upper():
        return False
    if m.path_glob is not None:
        glob, target = m.path_glob.lower(), path.lower()
        bare_prefix = glob[:-2] if glob.endswith("/*") else None  # "/x/*" also covers "/x"
        if not (fnmatch.fnmatchcase(target, glob) or target == bare_prefix):
            return False
    return True


def to_number(value) -> float:
    if isinstance(value, bool):
        raise Unreadable(f"{value!r} is not a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise Unreadable(f"{value!r} is not a number")
    if not math.isfinite(number):
        raise Unreadable(f"{value!r} is not a finite number")
    return number


def evaluate_condition(cond: Condition, fields: dict, required: bool = False) -> bool:
    """True if the rule's condition holds. A present-but-unreadable field raises Unreadable.
    A missing field means the rule does not apply (False), unless `required`: then the field
    should have been there, so it also raises Unreadable (the gate treats that as "ask a human")."""
    if fields.get(cond.field) is None:
        if required:
            raise Unreadable(f"'{cond.field}' is missing from the request")
        return False
    actual = fields[cond.field]
    if isinstance(cond.value, str):
        if cond.op not in ("==", "!="):
            raise Unreadable(f"rule compares '{cond.field}' with text using '{cond.op}'")
        return _OPS[cond.op](str(actual).strip(), cond.value)
    return _OPS[cond.op](to_number(actual), float(cond.value))
