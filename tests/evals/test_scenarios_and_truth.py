"""Scenario file loading and the independent ground-truth check."""
import sqlite3
from pathlib import Path

import pytest

from apps.company.seed import seed_database
from evals import ground_truth
from evals.scenarios import GroundTruth, ScenarioError, load_scenarios, select

ROOT = Path(__file__).resolve().parents[2]


# ---- scenarios.yaml -----------------------------------------------------------------

def test_the_real_scenario_file_loads_with_the_p0_scenarios():
    scenarios = load_scenarios(ROOT / "evals" / "scenarios.yaml")
    assert [s.id for s in scenarios] == ["S1", "S2", "S3", "S3-advisory", "S4", "S5-approve", "S5-deny", "S6", "S7", "S10"]
    by_id = {s.id: s for s in scenarios}
    assert by_id["S1"].faults == [] and by_id["S1"].audit_mode == "enforce" and by_id["S1"].seed_variant == "default"  # defaults apply
    assert by_id["S3"].faults == ["DATE_TRAP"] == by_id["S3-advisory"].faults and by_id["S3-advisory"].audit_mode == "advisory"
    assert by_id["S3"].task == by_id["S3-advisory"].task  # the same task in both audit modes
    assert by_id["S4"].faults == ["SAVE_BROKEN", "BUTTON_RENAME"]
    assert by_id["S2"].expect.no_write_before_human and len(by_id["S2"].scripted_human.clarification) == 2
    assert by_id["S5-deny"].expect.outcomes == ["needs_human"] and by_id["S5-deny"].expect.ground_truth.row_delta == {}
    assert by_id["S5-approve"].scripted_human.approval == ["approve"] and by_id["S5-deny"].scripted_human.approval == ["deny"]
    assert [s.id for s in scenarios if s.priority == "P1"] == ["S10"] and all(s.priority in ("P0", "P1") for s in scenarios)
    assert by_id["S6"].expect.ground_truth.changed_tables == ["erp_bills"] and by_id["S7"].expect.ground_truth.row_delta == {"erp_vendors": 1}


def test_ground_truth_in_the_file_is_written_by_hand_from_the_seed_not_from_runs():
    """The expected rows must be consistent with the seed data, so the expectations are checkable without any run."""
    scenarios = {s.id: s for s in load_scenarios(ROOT / "evals" / "scenarios.yaml")}
    seed_rows = {("INV-2041", 7): (48200.0, "2026-11-03"), ("INV-3107", 5): (80000.0, "2026-11-10")}
    for sid in ("S1", "S3", "S5-approve"):
        row = scenarios[sid].expect.ground_truth.rows[0].where
        assert seed_rows[(row["invoice_no"], row["vendor_id"])] == (row["amount"], row["due_date"])


@pytest.mark.parametrize("change, fragment", [
    ({"surprise": 1}, "surprise"),
    ({"seed_variant": "bill_exists"}, "unknown seed_variant"),
    ({"audit_mode": "lenient"}, "audit_mode"),
    ({"priority": "P9"}, "priority"),
    ({"expect": {"outcomes": ["winning"], "ground_truth": {}}}, "unknown outcome"),
    ({"expect": {"outcomes": ["verified_done"]}}, "ground_truth"),
])
def test_bad_scenarios_are_rejected_with_the_scenario_named(tmp_path, change, fragment):
    import yaml
    base = {"id": "X", "priority": "P0", "name": "n", "task": "t",
            "expect": {"outcomes": ["verified_done"], "ground_truth": {}}}
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({"scenarios": [{**base, **change}]}), encoding="utf-8")
    with pytest.raises(ScenarioError, match=f"scenario X: .*{fragment}"):
        load_scenarios(tmp_path / "s.yaml")


def test_duplicate_ids_and_empty_files_are_rejected(tmp_path):
    import yaml
    one = {"id": "X", "priority": "P0", "name": "n", "task": "t", "expect": {"outcomes": ["blocked"], "ground_truth": {}}}
    (tmp_path / "dupes.yaml").write_text(yaml.safe_dump({"scenarios": [one, one]}), encoding="utf-8")
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        load_scenarios(tmp_path / "dupes.yaml")
    (tmp_path / "empty.yaml").write_text("scenarios: []", encoding="utf-8")
    with pytest.raises(ScenarioError, match="no scenarios"):
        load_scenarios(tmp_path / "empty.yaml")


def test_selecting_scenarios_by_id():
    scenarios = load_scenarios(ROOT / "evals" / "scenarios.yaml")
    assert [s.id for s in select(scenarios, ["S4", "S1"])] == ["S4", "S1"]
    assert select(scenarios, None) == scenarios
    with pytest.raises(ScenarioError, match="unknown scenario id"):
        select(scenarios, ["S99"])


# ---- ground truth --------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    seed_database(tmp_path / "data")
    db = tmp_path / "data" / "company.db"
    return db, ground_truth.read_world(db)


def write(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


NEW_BILL = "INSERT INTO erp_bills (vendor_id, invoice_no, amount, due_date, status, created_via) VALUES (7, 'INV-2041', 48200, '2026-11-03', 'Open', 'api')"
ONE_BILL = GroundTruth.model_validate({
    "row_delta": {"erp_bills": 1},
    "rows": [{"table": "erp_bills", "where": {"vendor_id": 7, "invoice_no": "INV-2041", "amount": 48200.0, "due_date": "2026-11-03"}, "count": 1}]})


def test_an_untouched_world_matches_an_empty_expectation(world):
    db, before = world
    assert ground_truth.check(GroundTruth(), before, db).ok
    assert before.counts == {"erp_bills": 2, "erp_vendors": 3, "portal_invoices": 9, "portal_vendors": 3}


def test_the_expected_change_is_accepted(world):
    db, before = world
    write(db, NEW_BILL)
    assert ground_truth.check(ONE_BILL, before, db).ok


def test_wrong_content_extra_rows_and_missing_rows_are_all_reported(world):
    db, before = world
    write(db, NEW_BILL.replace("'2026-11-03'", "'2026-03-11'"))
    result = ground_truth.check(ONE_BILL, before, db)
    assert not result.ok and result.failures == ["erp_bills where {'vendor_id': 7, 'invoice_no': 'INV-2041', 'amount': 48200.0, "
                                                  "'due_date': '2026-11-03'}: found 0 row(s), expected 1"]
    write(db, NEW_BILL)
    write(db, NEW_BILL)
    assert "erp_bills: +3 rows, expected +1" in ground_truth.check(ONE_BILL, before, db).failures[0]


def test_a_side_effect_in_another_table_is_caught_even_without_a_row_count_change(world):
    db, before = world
    write(db, NEW_BILL)
    write(db, "UPDATE erp_vendors SET payment_terms_days = 99 WHERE id = 7")
    result = ground_truth.check(ONE_BILL, before, db)
    assert result.failures == ["erp_vendors: content changed but the scenario says it must not"]
    allowed = GroundTruth.model_validate({**ONE_BILL.model_dump(), "changed_tables": ["erp_vendors"]})
    assert ground_truth.check(allowed, before, db).ok


def test_editing_an_existing_row_counts_as_a_content_change(world):
    db, before = world
    write(db, "UPDATE erp_bills SET due_date = '2026-09-07' WHERE id = 1")
    assert ground_truth.check(GroundTruth(), before, db).failures == ["erp_bills: content changed but the scenario says it must not"]
    edit = GroundTruth.model_validate({"changed_tables": ["erp_bills"], "rows": [
        {"table": "erp_bills", "where": {"id": 1, "due_date": "2026-09-07"}, "count": 1}]})
    assert ground_truth.check(edit, before, db).ok


def test_a_deleted_row_and_a_missing_table_are_caught(world):
    db, before = world
    write(db, "DELETE FROM erp_bills WHERE id = 2")
    assert ground_truth.check(GroundTruth(), before, db).failures == ["erp_bills: -1 rows, expected +0"]
    assert ground_truth.check(GroundTruth.model_validate({"row_delta": {"nope": 1}}), before, db).failures[-1] == "nope: table does not exist"


def test_the_check_opens_the_database_read_only(world):
    db, before = world
    conn = ground_truth._connect(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM erp_bills")
    conn.close()
