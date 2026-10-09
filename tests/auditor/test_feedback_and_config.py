"""The feedback ladder and the YAML config loader."""
from pathlib import Path

from auditor.config import load_config
from auditor.feedback import feedback

NEW_ITEM = "INSERT INTO items (owner_id, label, amount, due) VALUES (1, 'second', 750.0, ?)"
SECRET_EXPECTED = "2026-04-02"
SECRET_ACTUAL = "2026-02-04"
BODY = """
assertions:
  - id: due
    kind: field_equals
    actual: {db: main, table: items, where: {label: second}, field: due}
    expected: {from: query, db: src, table: source_items, where: {owner_id: 1}, order_by: "listed_on desc", field: due}
  - {kind: rows_added, table: items, count: 1}
  - {kind: no_other_changes, except_tables: [items]}
"""


def failing_result(world, audit):
    world.write("main", NEW_ITEM, (SECRET_ACTUAL,))
    return audit(BODY)


def test_round_1_names_the_failed_check_and_field_but_no_values(world, audit):
    text = feedback(failing_result(world, audit), 1)
    assert "due (field_equals)" in text and "field 'due' of table 'items'" in text
    assert SECRET_EXPECTED not in text and SECRET_ACTUAL not in text
    assert "rows_added" not in text  # passing checks are not listed


def test_round_2_adds_expected_and_actual(world, audit):
    text = feedback(failing_result(world, audit), 2)
    assert SECRET_EXPECTED in text and SECRET_ACTUAL in text


def test_round_3_is_the_full_escalation_report(world, audit):
    result = failing_result(world, audit)
    text = feedback(result, 3)
    assert SECRET_EXPECTED in text and "main.items" in text and "Changes made during the run" in text


def test_validation_problems_are_always_shown_in_full(world, audit):
    world.write("main", NEW_ITEM, (SECRET_ACTUAL,))
    result = audit("assertions:\n  - {kind: rows_added, table: items, count: 1}")
    for round_no in (1, 2):
        text = feedback(result, round_no)
        assert text.startswith("AUDIT NOT RUN") and "no_other_changes" in text


# ---- config loader -------------------------------------------------------

def test_load_config_resolves_bases(tmp_path):
    yaml_file = tmp_path / "audit.yaml"
    yaml_file.write_text(
        "databases:\n  one: {base: data, file: a.db, tables: [t1, t2]}\n  two: {base: data, file: b.db}\n"
        "file_roots:\n  docs: {base: repo, path: documents}\n",
        encoding="utf-8",
    )
    config = load_config(yaml_file, {"data": Path("/d"), "repo": Path("/r")})
    assert config.databases["one"].path == Path("/d/a.db") and config.databases["one"].tables == ("t1", "t2")
    assert config.databases["two"].tables is None
    assert config.file_roots == {"docs": Path("/r/documents")}


def test_the_company_pack_config_loads(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    config = load_config(repo_root / "company_pack" / "audit.yaml", {"data": tmp_path / "d", "repo": tmp_path / "r"})
    assert set(config.databases) == {"erp", "portal"}
    assert config.databases["erp"].path == tmp_path / "d" / "company.db"
    assert config.file_roots["inputs"] == tmp_path / "r" / "inputs"
