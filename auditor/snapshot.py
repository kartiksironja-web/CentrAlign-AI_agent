"""Run-level snapshots: what every table looked like before and after a run.

Purpose: the runner (never the agent) takes a snapshot of every row of every
table when a run starts and again at audit time; `diff` says which rows were
added, changed or removed.
Why this design: this is what makes "nothing else changed" checkable without
trusting the agent's log of what it did. Each row gets a SHA-256 hash of its
values so change detection is a hash comparison; the values are kept too, so the
final report can show the evidence (what exactly was added or changed).
Rows are identified by the table's declared primary key, or by rowid if it has none.
How it can fail: a snapshot is only as good as its moment. A write that happens
after the "after" snapshot is not seen; a table that is not listed for a db
label is not watched.
"""
import hashlib
import json
from dataclasses import dataclass, field

from auditor.readonly import ReadOnlyDbs

TableKey = tuple[str, str]  # (db label, table)


def _json_default(value):
    return value.hex() if isinstance(value, (bytes, bytearray)) else str(value)


def row_hash(values: dict) -> str:
    text = json.dumps(values, sort_keys=True, default=_json_default)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Snapshot:
    # (db, table) -> row key -> (hash, values)
    rows: dict[TableKey, dict[str, tuple[str, dict]]] = field(default_factory=dict)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def take_snapshot(dbs: ReadOnlyDbs) -> Snapshot:
    snap = Snapshot()
    for label in dbs.labels():
        conn = dbs.conn(label)
        for table in dbs.tables(label):
            info = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
            key_cols = [name for _, name in sorted((r["pk"], r["name"]) for r in info if r["pk"])]
            table_rows: dict[str, tuple[str, dict]] = {}
            if key_cols:
                for r in conn.execute(f"SELECT * FROM {_q(table)}"):
                    values = dict(r)
                    key = ",".join(str(values[c]) for c in key_cols)
                    table_rows[key] = (row_hash(values), values)
            else:  # no primary key: fall back to rowid
                for r in conn.execute(f'SELECT rowid AS "_rowid_", * FROM {_q(table)}'):
                    values = dict(r)
                    key = str(values.pop("_rowid_"))
                    table_rows[key] = (row_hash(values), values)
            snap.rows[(label, table)] = table_rows
    return snap


@dataclass
class ChangedRow:
    before: dict
    after: dict


@dataclass
class TableDiff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[ChangedRow] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


@dataclass
class Diff:
    tables: dict[TableKey, TableDiff] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return all(t.is_empty for t in self.tables.values())

    def summary(self) -> dict[str, dict[str, int]]:
        """Only tables that changed: {'db.table': {'added': n, 'changed': n, 'removed': n}}."""
        return {
            f"{db}.{table}": {"added": len(d.added), "changed": len(d.changed), "removed": len(d.removed)}
            for (db, table), d in self.tables.items()
            if not d.is_empty
        }


def diff(before: Snapshot, after: Snapshot) -> Diff:
    result = Diff()
    for key in sorted(set(before.rows) | set(after.rows)):
        b, a = before.rows.get(key, {}), after.rows.get(key, {})
        result.tables[key] = TableDiff(
            added=[a[k][1] for k in a if k not in b],
            removed=[b[k][1] for k in b if k not in a],
            changed=[ChangedRow(b[k][1], a[k][1]) for k in a if k in b and a[k][0] != b[k][0]],
        )
    return result
