"""Every assertion kind, passing and failing, evaluated against real SQLite files."""
import json

from auditor.validate import Requirements

NEW_ITEM = "INSERT INTO items (owner_id, label, amount, due) VALUES (1, ?, ?, ?)"

DUE_CHECK = """
  - id: due
    kind: field_equals
    actual: {db: main, table: items, where: {label: second}, field: due}
    expected: {from: query, db: src, table: source_items, where: {owner_id: 1}, order_by: "listed_on desc", limit: 1, field: due}
"""
ADDED_ONE = "  - {kind: rows_added, table: items, count: 1}\n  - {kind: no_other_changes, except_tables: [items]}"


def one(result, check_id):
    [c] = [c for c in result.checks if c.id == check_id]
    return c


# ---- field_equals --------------------------------------------------------

def test_field_equals_passes_when_the_value_matches_the_independent_source(world, audit):
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    result = audit("assertions:\n" + DUE_CHECK + ADDED_ONE)
    assert result.passed
    assert (one(result, "due").expected, one(result, "due").actual) == ("2026-04-02", "2026-04-02")


def test_field_equals_fails_on_a_wrong_value_and_reports_both_sides(world, audit):
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-02-04"))  # day and month swapped
    result = audit("assertions:\n" + DUE_CHECK + ADDED_ONE)
    c = one(result, "due")
    assert not result.passed and not c.passed
    assert (c.expected, c.actual, c.field, c.table) == ("2026-04-02", "2026-02-04", "due", "items")
    assert one(result, "rows_added_2").passed  # the other checks are judged separately


def test_field_equals_fails_when_the_row_is_missing(audit):
    c = one(audit("assertions:\n" + DUE_CHECK), "due")
    assert not c.passed and "no row matches" in c.detail


def test_field_equals_fails_when_the_row_is_duplicated(world, audit):
    for _ in range(2):
        world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    result = audit("assertions:\n" + DUE_CHECK + "  - {kind: rows_added, table: items, count: 2}\n"
                   "  - {kind: no_other_changes, except_tables: [items]}")
    c = one(result, "due")
    assert not c.passed and "2 rows match" in c.detail
    assert one(result, "rows_added_2").passed  # the count alone would have passed: duplicates need field checks


def test_expected_query_matching_several_rows_without_order_by_fails(world, audit):
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    body = DUE_CHECK.replace('order_by: "listed_on desc", ', "")
    c = one(audit("assertions:\n" + body + ADDED_ONE), "due")
    assert not c.passed and "matched several rows" in c.detail


def test_expected_query_with_a_tie_on_order_by_fails(world, audit):
    world.write("src", "INSERT INTO source_items VALUES ('S9', 1, '2026-03-01', 1.0, '2026-09-09')")  # ties with S2
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    c = one(audit("assertions:\n" + DUE_CHECK + ADDED_ONE), "due")
    assert not c.passed and "tie on order_by" in c.detail


def test_expected_query_with_no_rows_fails(world, audit):
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    body = DUE_CHECK.replace("owner_id: 1", "owner_id: 99")
    c = one(audit("assertions:\n" + body + ADDED_ONE), "due")
    assert not c.passed and "matched no rows" in c.detail


def test_expected_from_a_document_with_conversions(world, audit):
    world.write("main", NEW_ITEM, ("second", 1250.50, "2026-11-03"))
    result = audit("""
assertions:
  - id: amount
    kind: field_equals
    actual: {db: main, table: items, where: {label: second}, field: amount}
    expected: {from: file, path: docs/doc.pdf, pattern: "Amount: ([\\\\d,\\\\.]+)", as: number}
  - id: due
    kind: field_equals
    actual: {db: main, table: items, where: {label: second}, field: due}
    expected: {from: file, path: docs/form.txt, pattern: "Due: (\\\\S+)", as: date_dmy}
""" + ADDED_ONE)
    assert result.passed, result.as_dict()


def test_document_value_that_does_not_fit_its_conversion_fails_the_check(world, audit):
    world.write("main", NEW_ITEM, ("second", 1.0, "x"))
    result = audit("""
assertions:
  - id: bad
    kind: field_equals
    actual: {db: main, table: items, where: {label: second}, field: amount}
    expected: {from: file, path: docs/form.txt, pattern: "Owner Name: (.+)", as: number}
""" + ADDED_ONE)
    c = one(result, "bad")
    assert not c.passed and "not a number" in c.detail


def test_literal_expected(world, audit):
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    ok = audit("assertions:\n  - {id: l, kind: field_equals, actual: {db: main, table: items, where: {label: second}, field: amount},"
               " expected: {from: literal, value: 750}}\n" + ADDED_ONE)
    assert ok.passed
    bad = audit("assertions:\n  - {id: l, kind: field_equals, actual: {db: main, table: items, where: {label: second}, field: amount},"
                " expected: {from: literal, value: 751}}\n" + ADDED_ONE)
    assert not one(bad, "l").passed


# ---- counting kinds ------------------------------------------------------

def test_row_exists_and_row_count(world, audit):
    result = audit("""
assertions:
  - {id: e1, kind: row_exists, table: owners, where: {name: Alpha}}
  - {id: e2, kind: row_exists, table: owners, where: {name: Nobody}}
  - {id: c1, kind: row_count, table: owners, count: 2}
  - {id: c2, kind: row_count, table: items, where: {owner_id: 1}, count: 5}
""")
    assert [(c.id, c.passed) for c in result.checks] == [("e1", True), ("e2", False), ("c1", True), ("c2", False)]
    assert (one(result, "c2").expected, one(result, "c2").actual) == (5, 1)


def test_rows_added_and_rows_changed_count_what_happened_during_the_run(world, audit):
    world.write("main", NEW_ITEM, ("a", 1.0, "d"))
    world.write("main", NEW_ITEM, ("b", 1.0, "d"))
    world.write("main", "UPDATE owners SET name = 'Alpha2' WHERE id = 1")
    result = audit("""
assertions:
  - {id: add2, kind: rows_added, table: items, count: 2}
  - {id: add1, kind: rows_added, table: items, count: 1}
  - {id: chg1, kind: rows_changed, table: owners, count: 1}
  - {id: chg0, kind: rows_changed, table: items, count: 0}
  - {id: none, kind: no_other_changes, except_tables: [items, owners]}
""")
    assert [(c.id, c.passed) for c in result.checks] == [
        ("add2", True), ("add1", False), ("chg1", True), ("chg0", True), ("none", True),
    ]


# ---- no_other_changes ----------------------------------------------------

def test_nothing_changed_and_nothing_declared_passes(audit):
    assert audit("assertions:\n  - {kind: no_other_changes}\n  - {kind: rows_added, table: items, count: 0}").passed


def test_a_change_in_an_unlisted_table_fails(world, audit):
    world.write("main", NEW_ITEM, ("a", 1.0, "d"))
    world.write("main", "UPDATE owners SET name = 'X' WHERE id = 2")
    result = audit("assertions:\n" + ADDED_ONE)
    c = one(result, "no_other_changes_2")
    assert not c.passed and "main.owners: 1 row(s) changed" in c.actual and "main.items" not in c.actual


def test_adding_a_row_to_a_listed_table_without_declaring_it_fails(world, audit):
    world.write("main", NEW_ITEM, ("a", 1.0, "d"))
    c = one(audit("assertions:\n  - {kind: no_other_changes, except_tables: [items]}"), "no_other_changes_1")
    assert not c.passed and "1 row(s) added" in c.actual


def test_quietly_editing_another_row_while_adding_one_fails(world, audit):
    world.write("main", NEW_ITEM, ("a", 1.0, "d"))
    world.write("main", "UPDATE items SET amount = 999 WHERE label = 'first'")
    result = audit("assertions:\n" + ADDED_ONE)
    c = one(result, "no_other_changes_2")
    assert one(result, "rows_added_1").passed and not c.passed and "1 row(s) changed" in c.actual


def test_declaring_rows_changed_lets_exactly_that_kind_through(world, audit):
    world.write("main", "UPDATE items SET due = '2026-09-09' WHERE label = 'first'")
    result = audit("assertions:\n  - {kind: rows_changed, table: items, count: 1}\n  - {kind: no_other_changes, except_tables: [items]}")
    assert result.passed


def test_removing_a_row_fails_even_in_a_listed_table(world, audit):
    world.write("main", "DELETE FROM items WHERE label = 'first'")
    c = one(audit("assertions:\n  - {kind: no_other_changes, except_tables: [items]}"), "no_other_changes_1")
    assert not c.passed and "1 row(s) removed" in c.actual


def test_a_change_in_the_source_system_is_also_seen(world, audit):
    world.write("src", "UPDATE source_items SET amount = 1 WHERE code = 'S1'")
    c = one(audit("assertions:\n  - {kind: no_other_changes}"), "no_other_changes_1")
    assert not c.passed and "src.source_items" in c.actual


# ---- result handling -----------------------------------------------------

def test_validation_failure_evaluates_nothing(world, audit):
    world.write("main", NEW_ITEM, ("a", 1.0, "d"))
    result = audit("assertions:\n  - {kind: rows_added, table: items, count: 1}")  # no_other_changes missing
    assert not result.valid and result.checks == [] and not result.passed


def test_an_error_inside_one_check_is_a_failed_check_not_a_crash(world, audit, monkeypatch):
    import sqlite3
    from auditor import engine

    real_select = engine.select_rows

    def flaky(dbs, db, table, *args, **kwargs):
        if table == "source_items":  # the source system "fails" after validation has passed
            raise sqlite3.OperationalError("disk I/O error")
        return real_select(dbs, db, table, *args, **kwargs)

    monkeypatch.setattr(engine, "select_rows", flaky)
    world.write("main", NEW_ITEM, ("a", 1.0, "d"))
    result = audit("assertions:\n" + DUE_CHECK.replace("label: second", "label: a") + ADDED_ONE)
    c = one(result, "due")
    assert not c.passed and "error while checking" in c.detail
    assert one(result, "rows_added_2").passed  # other checks still ran


def test_result_serialises_to_json_with_evidence(world, audit):
    world.write("main", NEW_ITEM, ("second", 750.0, "2026-04-02"))
    world.write("main", "UPDATE owners SET name = 'Alpha2' WHERE id = 1")
    data = audit("assertions:\n" + DUE_CHECK + ADDED_ONE).as_dict()
    json.dumps(data)  # must not raise
    assert data["passed"] is False  # owners changed and was not listed
    assert data["changes"]["main.items"]["added"][0]["label"] == "second"
    assert data["changes"]["main.owners"]["changed"][0]["after"]["name"] == "Alpha2"


def test_an_empty_result_is_never_a_pass(audit):
    result = audit("assertions:\n  - {kind: no_other_changes}")
    assert result.passed  # one real check, nothing changed
    result.checks.clear()
    assert not result.passed  # zero checks must not count as success
