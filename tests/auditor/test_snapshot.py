"""Snapshots and diffs: what was added, changed, removed."""
from auditor.snapshot import diff, row_hash, take_snapshot


def test_rows_are_keyed_by_primary_key_or_rowid(dbs):
    snap = take_snapshot(dbs)
    assert set(snap.rows[("main", "owners")]) == {"1", "2"}
    assert set(snap.rows[("main", "items")]) == {"1"}
    assert set(snap.rows[("src", "source_items")]) == {"S1", "S2", "S3", "S4"}
    assert set(snap.rows[("main", "notes")]) == {"1"}  # no pk: rowid
    assert snap.rows[("main", "notes")]["1"][1] == {"text": "hello"}  # rowid is not part of the values


def test_unchanged_world_has_an_empty_diff(dbs, before):
    d = diff(before, take_snapshot(dbs))
    assert d.is_empty and d.summary() == {}


def test_added_changed_removed(dbs, before, world):
    world.write("main", "INSERT INTO items (owner_id, label, amount, due) VALUES (1, 'second', 5.0, '2026-02-02')")
    world.write("main", "UPDATE owners SET name = 'Alpha2' WHERE id = 1")
    world.write("src", "DELETE FROM source_items WHERE code = 'S4'")
    world.write("main", "INSERT INTO notes VALUES ('another')")
    d = diff(before, take_snapshot(dbs))
    assert d.summary() == {
        "main.items": {"added": 1, "changed": 0, "removed": 0},
        "main.owners": {"added": 0, "changed": 1, "removed": 0},
        "main.notes": {"added": 1, "changed": 0, "removed": 0},
        "src.source_items": {"added": 0, "changed": 0, "removed": 1},
    }
    assert d.tables[("main", "items")].added[0]["label"] == "second"
    change = d.tables[("main", "owners")].changed[0]
    assert (change.before["name"], change.after["name"]) == ("Alpha", "Alpha2")
    assert d.tables[("src", "source_items")].removed[0]["code"] == "S4"


def test_rewriting_a_row_with_identical_values_is_not_a_change(dbs, before, world):
    world.write("main", "UPDATE owners SET name = 'Alpha' WHERE id = 1")
    assert diff(before, take_snapshot(dbs)).is_empty


def test_row_hash_depends_on_every_value_and_ignores_key_order():
    assert row_hash({"a": 1, "b": 2}) == row_hash({"b": 2, "a": 1})
    assert row_hash({"a": 1, "b": 2}) != row_hash({"a": 1, "b": 3})
    assert row_hash({"a": b"\x00\x01"})  # bytes are hashable
