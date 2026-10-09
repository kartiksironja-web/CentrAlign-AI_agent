"""The engine: evaluates validated assertions against the real databases.

Purpose: `run_audit` takes the run's "before" snapshot, snapshots again, validates
the declared assertions, evaluates each one, and returns an AuditResult.
Why this design, rule by rule:
  * field_equals fails (never passes) when the actual row is missing or when more
    than one row matches: a duplicated record is a failure, not a coin flip.
  * An expected value from a query must be unambiguous: several matching rows
    without order_by, or a tie on the order_by field, makes the check fail.
  * no_other_changes means: no row was removed anywhere; in tables NOT listed in
    except_tables nothing changed; in listed tables only the kinds of change that
    a rows_added / rows_changed assertion declares may happen (the count itself is
    verified by that assertion). So "added one row AND quietly edited another" fails.
  * Any error while checking one assertion becomes a FAILED check with the reason.
How it can fail: it checks what was asserted. rows_changed says how many rows
changed, not which columns; pin columns with field_equals on the fields that matter.
"""
import sqlite3
from dataclasses import asdict, dataclass, field

from auditor import files
from auditor.assertions import (
    ExpectedFile, ExpectedLiteral, ExpectedQuery,
    FieldEquals, NoOtherChanges, RowCount, RowExists, RowsAdded, RowsChanged,
)
from auditor.query import parse_order_by, select_rows
from auditor.readonly import ReadOnlyDbs
from auditor.snapshot import Diff, Snapshot, diff as make_diff, take_snapshot
from auditor.validate import Requirements, validate
from auditor.values import coerce, values_equal


@dataclass
class CheckResult:
    id: str
    kind: str
    passed: bool
    table: str | None = None
    field: str | None = None
    expected: object = None
    actual: object = None
    detail: str = ""


@dataclass
class AuditResult:
    validation_errors: list[str]
    checks: list[CheckResult]
    diff: Diff = field(default_factory=Diff)

    @property
    def valid(self) -> bool:
        return not self.validation_errors

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def passed(self) -> bool:
        return self.valid and bool(self.checks) and not self.failed

    def as_dict(self) -> dict:
        """JSON-safe form for run.json and the final report's evidence."""
        changes = {
            f"{db}.{table}": {
                "added": d.added,
                "removed": d.removed,
                "changed": [{"before": c.before, "after": c.after} for c in d.changed],
            }
            for (db, table), d in self.diff.tables.items()
            if not d.is_empty
        }
        return _jsonable({
            "passed": self.passed,
            "valid": self.valid,
            "validation_errors": self.validation_errors,
            "checks": [asdict(c) for c in self.checks],
            "changes": changes,
        })


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


class ExpectedError(Exception):
    pass


def _resolve_expected(expected, dbs: ReadOnlyDbs):
    """The value the actual field must equal, from an independent source."""
    if isinstance(expected, ExpectedLiteral):
        return expected.value
    try:
        if isinstance(expected, ExpectedQuery):
            rows = select_rows(dbs, expected.db, expected.table, expected.where, expected.order_by, limit=2)
            if not rows:
                raise ExpectedError("the expected-value query matched no rows")
            if expected.order_by is None and len(rows) > 1:
                raise ExpectedError("the expected-value query matched several rows; add order_by or narrow where")
            if expected.order_by is not None and len(rows) > 1:
                order_field = parse_order_by(expected.order_by)[0]
                if rows[0][order_field] == rows[1][order_field]:
                    raise ExpectedError(f"tie on order_by field '{order_field}'; the expected row is ambiguous")
            return coerce(rows[0][expected.field], expected.as_)
        if isinstance(expected, ExpectedFile):
            return coerce(files.extract(dbs.config, expected.path, expected.pattern, expected.group), expected.as_)
    except (LookupError, files.FileSourceError, ValueError) as e:
        raise ExpectedError(str(e.args[0]) if e.args else str(e))
    raise ExpectedError(f"unsupported expected source: {expected!r}")


def expected_problem(expected, dbs: ReadOnlyDbs) -> str | None:
    """Why this expected value cannot be obtained right now (None if it can). Used by the validator when
    assertions are DECLARED, so a pattern that matches nothing is rejected at once instead of failing the audit."""
    if isinstance(expected, ExpectedLiteral):
        return None
    try:
        _resolve_expected(expected, dbs)
    except ExpectedError as e:
        hint = ""
        if isinstance(expected, ExpectedFile):
            hint = " (copy the words before the value exactly as the document text prints them)"
            if "<" in expected.pattern or ">" in expected.pattern:
                hint += "; remove the angle brackets: < and > are not part of the document"
        return f"expected value unavailable: {e}{hint}"
    return None


def _field_equals(a: FieldEquals, dbs: ReadOnlyDbs) -> CheckResult:
    ref = a.actual
    base = dict(id=a.id, kind=a.kind, table=ref.table, field=ref.field)
    rows = select_rows(dbs, ref.db, ref.table, ref.where)
    if len(rows) != 1:
        why = "no row matches" if not rows else f"{len(rows)} rows match (expected exactly one)"
        return CheckResult(**base, passed=False, detail=f"{why} the 'actual' conditions")
    actual = rows[0][ref.field]
    try:
        expected = _resolve_expected(a.expected, dbs)
    except ExpectedError as e:
        return CheckResult(**base, passed=False, actual=actual, detail=f"expected value unavailable: {e}")
    return CheckResult(**base, passed=values_equal(actual, expected), expected=expected, actual=actual)


def _count_check(a, dbs: ReadOnlyDbs, expected_count: int | None, what: str) -> CheckResult:
    db = dbs.resolve_db(a.db, a.table)
    n = len(select_rows(dbs, db, a.table, a.where))
    passed = n >= 1 if expected_count is None else n == expected_count
    return CheckResult(
        id=a.id, kind=a.kind, passed=passed, table=a.table,
        expected=what if expected_count is None else expected_count, actual=n,
    )


def _change_count(a, dbs: ReadOnlyDbs, changes: Diff, what: str) -> CheckResult:
    db = dbs.resolve_db(a.db, a.table)
    d = changes.tables[(db, a.table)]
    n = len(d.added) if what == "added" else len(d.changed)
    return CheckResult(id=a.id, kind=a.kind, passed=n == a.count, table=a.table, expected=a.count, actual=n)


def _no_other_changes(a: NoOtherChanges, assertions: list, dbs: ReadOnlyDbs, changes: Diff) -> CheckResult:
    listed = set(a.except_tables)
    declared_added = {(dbs.resolve_db(x.db, x.table), x.table) for x in assertions if isinstance(x, RowsAdded)}
    declared_changed = {(dbs.resolve_db(x.db, x.table), x.table) for x in assertions if isinstance(x, RowsChanged)}
    problems, tables = [], []
    for (db, table), d in changes.tables.items():
        found = []
        if d.removed:
            found.append(f"{len(d.removed)} row(s) removed")
        if d.added and not (table in listed and (db, table) in declared_added):
            found.append(f"{len(d.added)} row(s) added")
        if d.changed and not (table in listed and (db, table) in declared_changed):
            found.append(f"{len(d.changed)} row(s) changed")
        if found:
            problems.append(f"{db}.{table}: {', '.join(found)}")
            tables.append(table)
    return CheckResult(
        id=a.id, kind=a.kind, passed=not problems, table=", ".join(tables) or None,
        expected="no changes beyond those declared", actual="; ".join(problems) or "none",
    )


def _evaluate(a, assertions: list, dbs: ReadOnlyDbs, changes: Diff) -> CheckResult:
    if isinstance(a, FieldEquals):
        return _field_equals(a, dbs)
    if isinstance(a, RowExists):
        return _count_check(a, dbs, None, "at least 1 row")
    if isinstance(a, RowCount):
        return _count_check(a, dbs, a.count, "")
    if isinstance(a, RowsAdded):
        return _change_count(a, dbs, changes, "added")
    if isinstance(a, RowsChanged):
        return _change_count(a, dbs, changes, "changed")
    return _no_other_changes(a, assertions, dbs, changes)


def run_audit(dbs: ReadOnlyDbs, assertions: list, requirements: Requirements, before: Snapshot) -> AuditResult:
    """Validate, then evaluate. If validation fails nothing is evaluated."""
    changes = make_diff(before, take_snapshot(dbs))
    errors = validate(assertions, dbs, requirements, changes)
    if errors:
        return AuditResult(validation_errors=errors, checks=[], diff=changes)
    checks = []
    for a in assertions:
        try:
            checks.append(_evaluate(a, assertions, dbs, changes))
        except (LookupError, files.FileSourceError, sqlite3.Error, ValueError) as e:
            checks.append(CheckResult(
                id=a.id, kind=a.kind, passed=False, detail=f"error while checking: {e}",
            ))
    return AuditResult(validation_errors=[], checks=checks, diff=changes)
