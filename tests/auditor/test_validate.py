"""The validator's deterministic rules: existence, coverage, independence, no_other_changes."""
import pytest

from auditor.assertions import parse_assertions_yaml
from auditor.engine import expected_problem
from auditor.snapshot import diff, take_snapshot
from auditor.validate import Requirements, validate

GOOD_FIELD = """
  - id: due
    kind: field_equals
    actual: {db: main, table: items, where: {label: first}, field: due}
    expected: {from: query, db: src, table: source_items, where: {owner_id: 1}, order_by: "listed_on desc", field: due}
"""


@pytest.fixture
def check(dbs, before):
    def _check(body: str, requirements=Requirements(), resolve=False):
        changes = diff(before, take_snapshot(dbs))
        return validate(parse_assertions_yaml("assertions:\n" + body), dbs, requirements, changes,
                        expected_problem if resolve else None)

    return _check


def test_a_good_set_has_no_errors(check):
    assert check(GOOD_FIELD) == []


# ---- rule 1: everything referenced must exist ----------------------------

@pytest.mark.parametrize("body, fragment", [
    ("  - {kind: field_equals, actual: {db: nope, table: items, where: {}, field: due}, expected: {from: literal, value: 1}}",
     "unknown db 'nope'"),
    ("  - {kind: field_equals, actual: {db: main, table: ghosts, where: {}, field: due}, expected: {from: literal, value: 1}}",
     "not available in db 'main'"),
    ("  - {kind: field_equals, actual: {db: main, table: items, where: {}, field: ghost}, expected: {from: literal, value: 1}}",
     "unknown field 'ghost'"),
    ("  - {kind: field_equals, actual: {db: main, table: items, where: {ghost: 1}, field: due}, expected: {from: literal, value: 1}}",
     "unknown field 'ghost'"),
    ("  - {kind: field_equals, actual: {db: main, table: items, where: {}, field: due}, expected: {from: query, db: src, table: source_items, field: ghost}}",
     "unknown field 'ghost'"),
    ("  - {kind: field_equals, actual: {db: main, table: items, where: {}, field: due}, expected: {from: query, db: src, table: source_items, field: due, order_by: 'x; DROP TABLE y'}}",
     "order_by"),
    ("  - {kind: field_equals, actual: {db: main, table: items, where: {}, field: due}, expected: {from: query, db: src, table: source_items, field: due, order_by: ghost desc}}",
     "unknown field 'ghost'"),
    ("  - {kind: row_exists, table: ghosts, where: {}}", "unknown table 'ghosts'"),
    ("  - {kind: row_count, table: items, where: {ghost: 1}, count: 1}", "unknown field 'ghost'"),
    ("  - {kind: rows_added, table: ghosts, count: 1}", "unknown table 'ghosts'"),
    ("  - {kind: rows_changed, db: src, table: items, count: 1}", "not available in db 'src'"),
    ("  - {kind: no_other_changes, except_tables: [ghosts]}", "unknown table 'ghosts'"),
])
def test_unknown_names_are_rejected(check, body, fragment):
    assert fragment in " | ".join(check(body))


def test_file_source_problems_are_rejected_before_evaluation(check):
    def src(path, pattern="Amount: (.+)"):
        return (f"  - {{kind: field_equals, actual: {{db: main, table: items, where: {{}}, field: amount}},"
                f" expected: {{from: file, path: '{path}', pattern: '{pattern}'}}}}")
    assert "leaves" in " ".join(check(src("docs/../main.db"))) or "'..'" in " ".join(check(src("docs/../main.db")))
    assert "unknown file root" in " ".join(check(src("elsewhere/form.txt")))
    assert "not found" in " ".join(check(src("docs/missing.txt")))
    assert "valid regular expression" in " ".join(check(src("docs/form.txt", "(oops")))
    assert check(src("docs/form.txt")) == []


# ---- rule 6: at declaration, expected values must be obtainable now ----------------------------

def _file_assertion(pattern):
    return ("  - {kind: field_equals, actual: {db: main, table: items, where: {}, field: amount},"
            f" expected: {{from: file, path: 'docs/form.txt', pattern: '{pattern}'}}}}")


def test_a_pattern_that_matches_nothing_is_rejected_at_declaration_but_left_to_the_audit_otherwise(check):
    body = _file_assertion("<Amount>: (.+)")  # the label copied with its angle brackets, as a real model did
    assert check(body) == []  # the final audit's validation does not look values up: the audit reports it as a failed check
    problems = " ".join(check(body, resolve=True))
    assert "expected value unavailable" in problems and "did not match" in problems and "remove the angle brackets" in problems
    wrong_words = " ".join(check(_file_assertion("Total: (.+)"), resolve=True))
    assert "did not match" in wrong_words and "angle brackets" not in wrong_words
    assert check(_file_assertion("Amount: (.+)"), resolve=True) == []


def test_a_lookup_that_finds_no_row_is_rejected_at_declaration(check):
    body = GOOD_FIELD.replace("owner_id: 1", "owner_id: 999")
    assert check(body) == []
    assert "the expected-value query matched no rows" in " ".join(check(body, resolve=True))
    assert check(GOOD_FIELD, resolve=True) == []


def test_names_are_checked_before_values_are_looked_up(check):
    body = GOOD_FIELD.replace("field: due}", "field: ghost}", 1).replace("owner_id: 1", "owner_id: 999")  # bad name AND no row
    errors = " ".join(check(body, resolve=True))
    assert "unknown field 'ghost'" in errors and "expected value unavailable" not in errors


# ---- rule 2: ids ---------------------------------------------------------

def test_duplicate_ids_are_rejected(check):
    body = "  - {id: x, kind: rows_added, table: items, count: 0}\n  - {id: x, kind: rows_changed, table: items, count: 0}"
    assert "duplicate assertion id 'x'" in " ".join(check(body))


# ---- rule 3: coverage of required fields ---------------------------------

REQ = Requirements(done_requires={"items": ["due", "amount"]})


def test_every_required_field_needs_a_field_equals(check):
    errors = check(GOOD_FIELD, REQ)
    assert errors == ["required field items.amount has no field_equals assertion"]


def test_coverage_is_by_table_and_field_not_by_other_assertion_kinds(check):
    body = GOOD_FIELD + "  - {kind: row_exists, table: items, where: {amount: 100}}"
    assert any("items.amount" in e for e in check(body, REQ))


def test_a_requirement_on_a_table_the_auditor_cannot_read_is_reported(check):
    errors = check(GOOD_FIELD, Requirements(done_requires={"mystery": ["x"]}))
    assert errors == ["the procedure requires checks on table 'mystery', which the auditor cannot read"]


# ---- rule 4: independence ------------------------------------------------

LITERAL_AMOUNT = """
  - id: amt
    kind: field_equals
    actual: {db: main, table: items, where: {label: first}, field: amount}
    expected: {from: literal, value: 100}
"""


def test_a_literal_expected_value_is_rejected_for_required_fields(check):
    errors = check(GOOD_FIELD + LITERAL_AMOUNT, REQ)
    assert len(errors) == 1 and "'amt'" in errors[0] and "independent source" in errors[0]


def test_a_literal_is_fine_when_the_procedure_allows_it(check):
    req = Requirements(done_requires={"items": ["due", "amount"]}, allow_literal={"items": ["amount"]})
    assert check(GOOD_FIELD + LITERAL_AMOUNT, req) == []


def test_a_literal_on_a_field_nobody_requires_is_fine(check):
    assert check(GOOD_FIELD + LITERAL_AMOUNT, Requirements(done_requires={"items": ["due"]})) == []


def test_adding_a_second_literal_assertion_does_not_hide_the_problem(check):
    body = GOOD_FIELD.replace("field: due}\n    expected", "field: due}\n    expected") + """
  - id: sneaky
    kind: field_equals
    actual: {db: main, table: items, where: {label: first}, field: due}
    expected: {from: literal, value: '2026-01-05'}
"""
    errors = check(body, Requirements(done_requires={"items": ["due"]}))
    assert any("'sneaky'" in e for e in errors)


# ---- rule 5: no_other_changes when data changed --------------------------

def test_no_other_changes_is_required_once_data_changed(check, world):
    body = "  - {kind: rows_added, table: items, count: 1}"
    assert check(body) == []  # nothing changed yet, so not required
    world.write("main", "INSERT INTO items (owner_id, label, amount, due) VALUES (1, 'x', 1, 'd')")
    errors = check(body)
    assert len(errors) == 1 and "no_other_changes" in errors[0] and "main.items" in errors[0]
    assert check(body + "\n  - {kind: no_other_changes, except_tables: [items]}") == []


def test_at_declaration_an_undeclared_added_row_is_refused_with_the_exact_fix(world, dbs, before):
    """Seen in a real run: no_other_changes listed the table but the added row was never declared, so the
    audit failed and the agent could not tell why. The audit itself is unchanged (it reports a failed check)."""
    world.write("main", "INSERT INTO items (owner_id, label, amount, due) VALUES (1, 'x', 1, 'd')")
    changes = diff(before, take_snapshot(dbs))

    def declare(body):
        return validate(parse_assertions_yaml("assertions:\n" + body), dbs, Requirements(), changes, declaring=True)

    listed_only = "  - {kind: no_other_changes, except_tables: [items]}"
    errors = " ".join(declare(listed_only))
    assert "would fail the audit" in errors and "1 row(s) were added to items" in errors
    assert "{kind: rows_added, table: items, count: 1}" in errors
    not_listed = "  - {kind: rows_added, table: items, count: 1}\n  - {kind: no_other_changes, except_tables: []}"
    assert "list items in except_tables" in " ".join(declare(not_listed))
    complete = "  - {kind: rows_added, table: items, count: 1}\n  - {kind: no_other_changes, except_tables: [items]}"
    assert declare(complete) == []
    # without declaring=True (the final audit) the same input is left to the engine to report as a failed check
    assert validate(parse_assertions_yaml("assertions:\n" + listed_only), dbs, Requirements(), changes) == []


def test_at_declaration_an_undeclared_changed_row_is_refused_too(world, dbs, before):
    world.write("main", "UPDATE items SET amount = amount + 1 WHERE label = 'first'")
    changes = diff(before, take_snapshot(dbs))
    body = "assertions:\n  - {kind: no_other_changes, except_tables: [items]}"
    errors = " ".join(validate(parse_assertions_yaml(body), dbs, Requirements(), changes, declaring=True))
    assert "row(s) of items were changed" in errors and "{kind: rows_changed, table: items, count: 1}" in errors
