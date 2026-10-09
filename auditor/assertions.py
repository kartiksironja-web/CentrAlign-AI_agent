"""The assertion language: what the agent declares must be true, as DATA.

Purpose: typed models for the six assertion kinds and a parser that turns YAML or
plain dicts into them, with error messages written so a model can fix its own input.
  field_equals      one field of one row equals an independently obtained value
  row_exists        at least one row matches
  row_count         exactly N rows match
  rows_added        N rows were added to a table during the run
  rows_changed      N existing rows of a table changed during the run
  no_other_changes  nothing else changed (see engine.py for the exact rule)
Where an expected value comes from: `query` (a row in another table or database),
`file` (regex over a document), or `literal` (only if the written procedure allows).
Why this design: unknown keys are rejected (extra="forbid") so a typo like
"expcted" fails loudly instead of being silently ignored.
How it can fail: this module only checks SHAPE. Whether the tables and fields
exist, and whether the checks are strong enough, is validate.py's job.
"""
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

Scalar = Union[str, int, float, None]
AsKind = Literal["text", "number", "date_dmy"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Ref(_Strict):
    """Points at one field of the single row matched by `where`."""
    db: str
    table: str
    where: dict[str, Scalar]
    field: str


class ExpectedQuery(_Strict):
    from_: Literal["query"] = Field(alias="from")
    db: str
    table: str
    where: dict[str, Scalar] = Field(default_factory=dict)
    order_by: str | None = None
    limit: Literal[1] = 1
    field: str
    as_: AsKind | None = Field(default=None, alias="as")


class ExpectedFile(_Strict):
    from_: Literal["file"] = Field(alias="from")
    path: str
    pattern: str
    group: int = 1
    as_: AsKind | None = Field(default=None, alias="as")


class ExpectedLiteral(_Strict):
    from_: Literal["literal"] = Field(alias="from")
    value: Scalar


Expected = Annotated[Union[ExpectedQuery, ExpectedFile, ExpectedLiteral], Field(discriminator="from_")]


class _Base(_Strict):
    id: str | None = None


class FieldEquals(_Base):
    kind: Literal["field_equals"]
    actual: Ref
    expected: Expected


class RowExists(_Base):
    kind: Literal["row_exists"]
    db: str | None = None
    table: str
    where: dict[str, Scalar]


class RowCount(_Base):
    kind: Literal["row_count"]
    db: str | None = None
    table: str
    where: dict[str, Scalar] = Field(default_factory=dict)
    count: int = Field(ge=0)


class RowsAdded(_Base):
    kind: Literal["rows_added"]
    db: str | None = None
    table: str
    count: int = Field(ge=0)


class RowsChanged(_Base):
    kind: Literal["rows_changed"]
    db: str | None = None
    table: str
    count: int = Field(ge=0)


class NoOtherChanges(_Base):
    kind: Literal["no_other_changes"]
    except_tables: list[str] = Field(default_factory=list)


Assertion = Annotated[
    Union[FieldEquals, RowExists, RowCount, RowsAdded, RowsChanged, NoOtherChanges],
    Field(discriminator="kind"),
]
KINDS = ["field_equals", "row_exists", "row_count", "rows_added", "rows_changed", "no_other_changes"]
_adapter = TypeAdapter(Assertion)


class AssertionFormatError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_assertions(data) -> list:
    """Accepts {'assertions': [...]} or a bare list. Raises AssertionFormatError listing every problem."""
    if isinstance(data, dict):
        if "assertions" not in data:
            raise AssertionFormatError(["expected a top-level 'assertions:' list"])
        data = data["assertions"]
    if not isinstance(data, list) or not data:
        raise AssertionFormatError(["'assertions' must be a non-empty list"])

    parsed, errors = [], []
    for i, item in enumerate(data, start=1):
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind not in KINDS:
            errors.append(f"assertion {i}: 'kind' must be one of {KINDS}, got {kind!r}")
            continue
        try:
            a = _adapter.validate_python(item)
        except ValidationError as e:
            for err in e.errors():
                where = ".".join(str(p) for p in err["loc"][1:])  # [0] is the kind tag
                errors.append(f"assertion {i} ({kind}): {where or '(item)'}: {err['msg']}")
            continue
        a.id = a.id or f"{a.kind}_{i}"
        parsed.append(a)
    if errors:
        raise AssertionFormatError(errors)
    return parsed


def parse_assertions_yaml(text: str) -> list:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        hint = ""
        if "escape character" in str(e):  # the usual cause: a regex such as "\s" inside a double-quoted string
            hint = ("\nHOW TO FIX: YAML does not allow a backslash followed by s, d, w ... inside double quotes. "
                    "Write each regular expression in SINGLE quotes, e.g. pattern: 'Reference code:\\s*([A-Z0-9-]+)'.")
        raise AssertionFormatError([f"not valid YAML: {e}{hint}"])
    return parse_assertions(data)
