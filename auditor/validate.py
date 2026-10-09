"""The validator: rejects assertions that are structurally wrong or too weak.

Purpose: before any assertion is evaluated, check it against the real schema and
the written procedure's requirements. Deterministic rules, no LLM:
  1. every db, table, field, order_by field and file source must exist/be usable;
  2. assertion ids are unique;
  3. every field the procedure lists in `done_requires` is covered by at least one
     field_equals on exactly that table.field;
  4. for those fields `expected` must come from an independent source (query or
     file), not a literal, unless the procedure lists the field in `allow_literal`;
  5. if the run changed any data, a no_other_changes assertion must be present;
  6. only when the caller passes `expected_problem` (at declaration, not in the final audit): every
     expected value must be obtainable now (the pattern matches the document, the query finds its row),
     so a wrong pattern is rejected at once rather than failing the audit later;
  7. only with `declaring=True`: a no_other_changes that the audit would reject because an added or
     changed row was not declared (rows_added / rows_changed) is refused now, with the exact fix.
Why this design: the agent writes its own assertions, so the code decides how
strong they must be. Rule 3 means the agent cannot "forget" to check the amount;
rule 4 means it cannot check a value against itself.
How it can fail: it can only enforce what the procedure wrote down. A procedure
with an empty `done_requires` gets only rules 1, 2 and 5. Messages are returned
as plain strings so the agent can read them.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from auditor import files
from auditor.assertions import (
    FieldEquals, NoOtherChanges, RowCount, RowExists, RowsAdded, RowsChanged,
    ExpectedFile, ExpectedLiteral, ExpectedQuery,
)
from auditor.query import check_fields, parse_order_by
from auditor.readonly import ReadOnlyDbs
from auditor.snapshot import Diff


@dataclass(frozen=True)
class Requirements:
    """What the active procedure(s) demand. Both map table name -> field names."""
    done_requires: dict[str, list[str]] = field(default_factory=dict)
    allow_literal: dict[str, list[str]] = field(default_factory=dict)


def _problem(fn) -> str | None:
    """Run a checking function; turn a lookup/file error into its message."""
    try:
        fn()
    except (LookupError, files.FileSourceError) as e:
        return str(e.args[0]) if e.args else str(e)
    return None


def _check_expected(expected, dbs: ReadOnlyDbs) -> str | None:
    if isinstance(expected, ExpectedQuery):
        def check():
            check_fields(dbs, expected.db, expected.table, [*expected.where, expected.field])
            if expected.order_by:
                check_fields(dbs, expected.db, expected.table, [parse_order_by(expected.order_by)[0]])
        return _problem(check)
    if isinstance(expected, ExpectedFile):
        return files.check(dbs.config, expected.path, expected.pattern, expected.group)
    return None  # literal: nothing to look up


def _check_one(a, dbs: ReadOnlyDbs, expected_problem=None) -> list[str]:
    problems = []
    if isinstance(a, FieldEquals):
        ref = a.actual
        problems.append(_problem(lambda: check_fields(dbs, ref.db, ref.table, [*ref.where, ref.field])))
        problems.append(_check_expected(a.expected, dbs))
        if expected_problem and not any(problems):  # only look the value up once the names are known to exist
            problems.append(expected_problem(a.expected, dbs))
    elif isinstance(a, (RowExists, RowCount)):
        def check():
            db = dbs.resolve_db(a.db, a.table)
            check_fields(dbs, db, a.table, a.where)
        problems.append(_problem(check))
    elif isinstance(a, (RowsAdded, RowsChanged)):
        problems.append(_problem(lambda: dbs.resolve_db(a.db, a.table)))
    elif isinstance(a, NoOtherChanges):
        for table in a.except_tables:
            problems.append(_problem(lambda t=table: dbs.resolve_db(None, t)))
    return [f"assertion '{a.id}': {p}" for p in problems if p]


def _undeclared_changes(a: NoOtherChanges, assertions: list, dbs: ReadOnlyDbs, diff: Diff) -> list[str]:
    """What the audit would reject in this no_other_changes right now, with the exact fix.
    Mirrors engine._no_other_changes for added and changed rows (removed rows can only fail the audit)."""
    listed = set(a.except_tables)

    def declared(kind) -> set:
        found = set()
        for x in assertions:
            if isinstance(x, kind):
                try:
                    found.add((dbs.resolve_db(x.db, x.table), x.table))
                except LookupError:
                    pass  # unknown tables are reported by the existence rule
        return found

    added, changed = declared(RowsAdded), declared(RowsChanged)
    problems = []
    for (db, table), d in diff.tables.items():
        if d.added and not (table in listed and (db, table) in added):
            problems.append(f"{len(d.added)} row(s) were added to {table}: declare "
                            f"{{kind: rows_added, table: {table}, count: {len(d.added)}}} and list {table} in except_tables")
        if d.changed and not (table in listed and (db, table) in changed):
            problems.append(f"{len(d.changed)} row(s) of {table} were changed: declare "
                            f"{{kind: rows_changed, table: {table}, count: {len(d.changed)}}} and list {table} in except_tables")
    return [f"assertion '{a.id}' would fail the audit: {p}" for p in problems]


def validate(assertions: list, dbs: ReadOnlyDbs, requirements: Requirements, diff: Diff,
             expected_problem=None, declaring: bool = False) -> list[str]:
    errors: list[str] = []

    seen = set()
    for a in assertions:
        if a.id in seen:
            errors.append(f"duplicate assertion id '{a.id}'")
        seen.add(a.id)
        errors.extend(_check_one(a, dbs, expected_problem))

    covering: dict[tuple[str, str], list[FieldEquals]] = defaultdict(list)
    for a in assertions:
        if isinstance(a, FieldEquals):
            covering[(a.actual.table, a.actual.field)].append(a)
    for table, fields in requirements.done_requires.items():
        if _problem(lambda t=table: dbs.resolve_db(None, t)):
            errors.append(f"the procedure requires checks on table '{table}', which the auditor cannot read")
            continue
        for f in fields:
            checks = covering.get((table, f), [])
            if not checks:
                errors.append(f"required field {table}.{f} has no field_equals assertion")
            for a in checks:
                literal_ok = f in requirements.allow_literal.get(table, [])
                if isinstance(a.expected, ExpectedLiteral) and not literal_ok:
                    errors.append(
                        f"assertion '{a.id}': expected value of {table}.{f} must come from an "
                        f"independent source (from: query or from: file), not a literal"
                    )

    if not diff.is_empty and not any(isinstance(a, NoOtherChanges) for a in assertions):
        changed = sorted(diff.summary())
        errors.append(
            f"data changed during this run ({', '.join(changed)}); declare a no_other_changes assertion"
        )
    if declaring:
        for a in assertions:
            if isinstance(a, NoOtherChanges):
                errors.extend(_undeclared_changes(a, assertions, dbs, diff))
    return errors
