"""The auditor's own channel really is read-only, and knows only what it was told."""
import shutil
import sqlite3

import pytest

from auditor.config import AuditConfig, DbSpec
from auditor.readonly import ConfigError, ReadOnlyDbs, open_readonly


def test_a_write_through_the_readonly_connection_is_refused(world):
    conn = open_readonly(world.main_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO owners VALUES (9, 'Mallory')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DROP TABLE owners")
    conn.close()
    check = sqlite3.connect(world.main_path)
    assert check.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 2
    check.close()


def test_readonly_open_never_creates_a_missing_file(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        open_readonly(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()


def test_path_with_spaces_works(world, tmp_path):
    spaced = tmp_path / "a folder with spaces"
    spaced.mkdir()
    shutil.copy(world.main_path, spaced / "copy.db")
    conn = open_readonly(spaced / "copy.db")
    assert conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 2
    conn.close()


def test_schema_is_read_and_limited_to_listed_tables(dbs):
    assert dbs.labels() == ["main", "src"]
    assert dbs.tables("main") == ["owners", "items", "notes"] or set(dbs.tables("main")) == {"owners", "items", "notes"}
    assert dbs.columns("main", "items") == ["id", "owner_id", "label", "amount", "due"]


def test_unlisted_tables_are_invisible(world):
    config = AuditConfig({"main": DbSpec(world.main_path, ("owners",))})
    with ReadOnlyDbs(config) as dbs:
        assert dbs.tables("main") == ["owners"]
        with pytest.raises(LookupError, match="not available"):
            dbs.columns("main", "items")


def test_listing_a_table_that_does_not_exist_is_a_loud_config_error(world):
    config = AuditConfig({"main": DbSpec(world.main_path, ("owners", "typo_table"))})
    with pytest.raises(ConfigError, match="typo_table"):
        ReadOnlyDbs(config)


def test_no_listed_tables_means_every_user_table_but_not_sqlite_internals(world):
    world.write("main", "INSERT INTO items (owner_id, label, amount, due) VALUES (1, 'x', 1, 'd')")  # creates sqlite_sequence
    with ReadOnlyDbs(AuditConfig({"main": DbSpec(world.main_path, None)})) as dbs:
        assert set(dbs.tables("main")) == {"owners", "items", "notes"}


def test_resolve_db_rules(dbs):
    assert dbs.resolve_db(None, "owners") == "main"
    assert dbs.resolve_db("src", "source_items") == "src"
    with pytest.raises(LookupError, match="unknown db"):
        dbs.resolve_db("nope", "owners")
    with pytest.raises(LookupError, match="unknown table"):
        dbs.resolve_db(None, "nope")
    with pytest.raises(LookupError, match="not available in db 'src'"):
        dbs.resolve_db("src", "owners")


def test_same_table_name_in_two_dbs_must_name_the_db(world):
    config = AuditConfig({"a": DbSpec(world.main_path, ("owners",)), "b": DbSpec(world.main_path, ("owners",))})
    with ReadOnlyDbs(config) as dbs:
        with pytest.raises(LookupError, match="several dbs"):
            dbs.resolve_db(None, "owners")
        assert dbs.resolve_db("a", "owners") == "a"
