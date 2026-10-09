"""Ground truth: did the world end up the way the scenario says it should?

Purpose: after a run, compare the final database with the scenario's hand-written expectation.
Why this design: this check is deliberately NOT the auditor and does not use anything the agent
declared. If the auditor ever passes a run that is wrong, this check is what exposes it
("auditor false pass"). It uses plain read-only SQLite and its own few lines of code, so a bug
in the auditor cannot hide here.
How it can fail: it checks only what the scenario states (row counts per table, content of
tables not allowed to change, specific rows); a wrong side effect outside those is not seen.
"""
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from evals.scenarios import GroundTruth


@dataclass
class WorldState:
    counts: dict[str, int] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class GroundResult:
    ok: bool
    failures: list[str]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)


def read_world(db_path: Path) -> WorldState:
    conn = _connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        world = WorldState()
        for table in tables:
            rows = sorted(json.dumps(list(r), default=str) for r in conn.execute(f"SELECT * FROM {_q(table)}"))
            world.counts[table] = len(rows)
            world.hashes[table] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        return world
    finally:
        conn.close()


def check(expected: GroundTruth, before: WorldState, db_path: Path) -> GroundResult:
    after = read_world(db_path)
    failures = []
    for table in sorted(after.counts):
        delta = after.counts[table] - before.counts.get(table, 0)
        want = expected.row_delta.get(table, 0)
        if delta != want:
            failures.append(f"{table}: {delta:+d} rows, expected {want:+d}")
        elif table not in expected.row_delta and table not in expected.changed_tables \
                and after.hashes[table] != before.hashes.get(table):
            failures.append(f"{table}: content changed but the scenario says it must not")
    for table in expected.row_delta:
        if table not in after.counts:
            failures.append(f"{table}: table does not exist")
    conn = _connect(db_path)
    try:
        for want in expected.rows:
            sql = f"SELECT COUNT(*) FROM {_q(want.table)}"
            if want.where:
                sql += " WHERE " + " AND ".join(f"{_q(col)} IS ?" for col in want.where)
            found = conn.execute(sql, list(want.where.values())).fetchone()[0]
            if found != want.count:
                failures.append(f"{want.table} where {want.where}: found {found} row(s), expected {want.count}")
    finally:
        conn.close()
    return GroundResult(not failures, failures)
