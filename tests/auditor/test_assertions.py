"""The assertion language: shape checking and error messages a model can act on."""
import pytest

from auditor.assertions import (
    AssertionFormatError, ExpectedFile, ExpectedLiteral, ExpectedQuery, FieldEquals,
    NoOtherChanges, RowCount, RowExists, RowsAdded, RowsChanged, parse_assertions, parse_assertions_yaml,
)

FULL = """
assertions:
  - id: due
    kind: field_equals
    actual: {db: main, table: items, where: {label: second}, field: due}
    expected: {from: query, db: src, table: source_items, where: {owner_id: 1}, order_by: "listed_on desc", limit: 1, field: due}
  - {kind: field_equals, actual: {db: main, table: items, where: {label: second}, field: amount},
     expected: {from: file, path: docs/form.txt, pattern: "Amount: (.+)", group: 1, as: number}}
  - {kind: field_equals, actual: {db: main, table: items, where: {label: second}, field: label},
     expected: {from: literal, value: second}}
  - {kind: row_exists, table: items, where: {label: second}}
  - {kind: row_count, table: items, where: {owner_id: 1}, count: 2}
  - {kind: rows_added, table: items, count: 1}
  - {kind: rows_changed, table: owners, count: 0}
  - {kind: no_other_changes, except_tables: [items]}
"""


def test_every_kind_parses():
    parsed = parse_assertions_yaml(FULL)
    assert [type(a) for a in parsed] == [
        FieldEquals, FieldEquals, FieldEquals, RowExists, RowCount, RowsAdded, RowsChanged, NoOtherChanges,
    ]
    assert isinstance(parsed[0].expected, ExpectedQuery) and parsed[0].expected.order_by == "listed_on desc"
    assert isinstance(parsed[1].expected, ExpectedFile) and parsed[1].expected.as_ == "number"
    assert isinstance(parsed[2].expected, ExpectedLiteral) and parsed[2].expected.value == "second"


def test_missing_ids_are_assigned_by_position():
    ids = [a.id for a in parse_assertions_yaml(FULL)]
    assert ids[0] == "due" and ids[1] == "field_equals_2" and ids[-1] == "no_other_changes_8"


def test_a_bare_list_is_accepted():
    assert len(parse_assertions([{"kind": "rows_added", "table": "t", "count": 1}])) == 1


@pytest.mark.parametrize("data, fragment", [
    ({"nope": []}, "top-level 'assertions:'"),
    ({"assertions": []}, "non-empty list"),
    ({"assertions": "x"}, "non-empty list"),
    ({"assertions": [{"kind": "wibble"}]}, "'kind' must be one of"),
    ({"assertions": [{"table": "t"}]}, "'kind' must be one of"),
    ({"assertions": ["text"]}, "'kind' must be one of"),
    ({"assertions": [{"kind": "rows_added", "table": "t"}]}, "count: Field required"),
    ({"assertions": [{"kind": "rows_added", "table": "t", "count": -1}]}, "count"),
    ({"assertions": [{"kind": "rows_added", "table": "t", "count": 1, "cnt": 2}]}, "cnt: Extra inputs are not permitted"),
    ({"assertions": [{"kind": "row_exists", "table": "t"}]}, "where: Field required"),
])
def test_bad_shapes_give_readable_errors(data, fragment):
    with pytest.raises(AssertionFormatError) as e:
        parse_assertions(data)
    assert fragment in " | ".join(e.value.errors)


def test_typo_in_a_nested_key_is_caught_with_its_location():
    text = """
assertions:
  - kind: field_equals
    actual: {db: main, table: items, where: {label: x}, field: due}
    expcted: {from: literal, value: 1}
"""
    with pytest.raises(AssertionFormatError) as e:
        parse_assertions_yaml(text)
    joined = " ".join(e.value.errors)
    assert "assertion 1 (field_equals)" in joined and "expcted" in joined and "expected: Field required" in joined


def test_unknown_expected_source_and_as_conversion_are_rejected():
    base = {"kind": "field_equals", "actual": {"db": "m", "table": "t", "where": {}, "field": "f"}}
    with pytest.raises(AssertionFormatError):
        parse_assertions([{**base, "expected": {"from": "guess", "value": 1}}])
    with pytest.raises(AssertionFormatError):
        parse_assertions([{**base, "expected": {"from": "file", "path": "a/b.txt", "pattern": "x", "as": "wibble"}}])


def test_all_problems_are_reported_together():
    with pytest.raises(AssertionFormatError) as e:
        parse_assertions([{"kind": "rows_added"}, {"kind": "wibble"}, {"kind": "row_count", "table": "t"}])
    assert len(e.value.errors) >= 3


def test_invalid_yaml():
    with pytest.raises(AssertionFormatError, match="not valid YAML"):
        parse_assertions_yaml("assertions: [unclosed")


REGEX_ASSERTION = """\
assertions:
  - kind: field_equals
    actual: {db: d, table: t, where: {k: 1}, field: f}
    expected: {from: file, path: docs/a.pdf, pattern: %s, group: 1, as: number}
"""


def test_regex_in_double_quotes_is_explained():
    """Seen in a real run: a model wrote "Amount:\\s*(...)" in double quotes 22 times, because the error was cryptic."""
    with pytest.raises(AssertionFormatError) as e:
        parse_assertions_yaml(REGEX_ASSERTION % '"Amount:\\s*([0-9,.]+)"')
    assert "SINGLE quotes" in e.value.errors[0]


def test_regex_in_single_quotes_is_accepted():
    (assertion,) = parse_assertions_yaml(REGEX_ASSERTION % "'Amount:\\s*([0-9,.]+)'")
    assert assertion.expected.pattern == "Amount:\\s*([0-9,.]+)"


def test_the_format_shown_to_the_model_uses_single_quoted_regexes():
    from agent.prompts import ASSERTION_FORMAT
    assert "pattern: '<regex" in ASSERTION_FORMAT and 'pattern: "' not in ASSERTION_FORMAT
