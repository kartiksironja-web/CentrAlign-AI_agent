"""The only SQL builder: names must exist, values are parameters, nothing is raw."""
import pytest

from auditor.query import parse_order_by, select_rows


def test_select_by_equality_and_order(dbs):
    rows = select_rows(dbs, "src", "source_items", {"owner_id": 1}, order_by="listed_on desc")
    assert [r["code"] for r in rows] == ["S2", "S1", "S3"]
    assert [r["code"] for r in select_rows(dbs, "src", "source_items", {"owner_id": 1}, "listed_on", limit=1)] == ["S3"]


def test_values_are_data_not_sql(dbs, world):
    evil = "x' OR '1'='1"
    assert select_rows(dbs, "main", "items", {"label": evil}) == []
    assert select_rows(dbs, "main", "items", {"label": "first'; DROP TABLE items; --"}) == []
    assert len(select_rows(dbs, "main", "items")) == 1  # still there


def test_unknown_names_are_rejected_with_the_valid_ones_listed(dbs):
    with pytest.raises(LookupError, match="unknown field 'nope'.*owner_id"):
        select_rows(dbs, "main", "items", {"nope": 1})
    with pytest.raises(LookupError, match="table 'nope' is not available in db 'main'.*owners"):
        select_rows(dbs, "main", "nope")
    with pytest.raises(LookupError, match="unknown field"):
        select_rows(dbs, "main", "items", order_by="ghost desc")


def test_identifier_injection_is_rejected(dbs):
    with pytest.raises(LookupError):
        select_rows(dbs, "main", "items", {'label" OR 1=1 --': "x"})
    with pytest.raises(LookupError):
        select_rows(dbs, "main", "items; DROP TABLE items")


def test_order_by_grammar():
    assert parse_order_by("due") == ("due", "ASC")
    assert parse_order_by(" due  DESC ") == ("due", "DESC")
    for bad in ("due; DROP TABLE items", "due desc, id", "due sideways", "", "due desc --"):
        with pytest.raises(LookupError, match="order_by"):
            parse_order_by(bad)


def test_none_matches_null(dbs, world):
    world.write("main", "INSERT INTO items (owner_id, label, amount, due) VALUES (2, 'blank', 1, NULL)")
    rows = select_rows(dbs, "main", "items", {"due": None})
    assert [r["label"] for r in rows] == ["blank"]


def test_text_value_matches_integer_column(dbs):
    assert len(select_rows(dbs, "main", "items", {"owner_id": "1"})) == 1
