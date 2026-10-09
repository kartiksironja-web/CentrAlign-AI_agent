"""Read-only access to the databases the auditor checks.

Purpose: ReadOnlyDbs opens one connection per database label, read-only, and
remembers which tables and columns exist.
Why this design: SQLite's `mode=ro` URI flag makes the OS-level open read-only,
and `PRAGMA query_only` is a second lock on top. So even a bug in this package
cannot change what it is auditing. The known tables and columns are what the
rest of the package checks every name against before it builds any SQL.
How it can fail: opening a path that does not exist raises (read-only mode
never creates a file); a label that lists a table missing from the file raises
ConfigError so a typo in the pack is not silently ignored.
"""
import sqlite3
from pathlib import Path

from auditor.config import AuditConfig, DbSpec


class ConfigError(Exception):
    pass


def open_readonly(path: Path) -> sqlite3.Connection:
    # as_uri() percent-encodes spaces and handles Windows drive letters.
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class ReadOnlyDbs:
    def __init__(self, config: AuditConfig):
        self.config = config
        self._conns: dict[str, sqlite3.Connection] = {}
        self._columns: dict[str, dict[str, list[str]]] = {}
        try:
            for label, spec in config.databases.items():
                conn = open_readonly(spec.path)
                self._conns[label] = conn
                self._columns[label] = self._read_schema(label, conn, spec)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _read_schema(label: str, conn: sqlite3.Connection, spec: DbSpec) -> dict[str, list[str]]:
        existing = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        wanted = list(spec.tables) if spec.tables else existing
        missing = [t for t in wanted if t not in existing]
        if missing:
            raise ConfigError(f"db '{label}' lists table(s) {missing} that do not exist in {spec.path}")
        return {
            t: [r["name"] for r in conn.execute(f"PRAGMA table_info({_quote(t)})")] for t in wanted
        }

    # --- what exists ---------------------------------------------------
    def labels(self) -> list[str]:
        return list(self._conns)

    def tables(self, label: str) -> list[str]:
        return list(self._columns[label])

    def conn(self, label: str) -> sqlite3.Connection:
        return self._conns[label]

    def columns(self, label: str, table: str) -> list[str]:
        """Column names of a table, or LookupError if db or table is unknown."""
        self.resolve_db(label, table)
        return self._columns[label][table]

    def resolve_db(self, db: str | None, table: str) -> str:
        """Which db label owns this table? If `db` is given it must own it; if not, the
        table name must belong to exactly one label. Raises LookupError with a message
        the agent can act on."""
        if db is not None:
            if db not in self._columns:
                raise LookupError(f"unknown db '{db}'; known dbs: {self.labels()}")
            if table not in self._columns[db]:
                raise LookupError(f"table '{table}' is not available in db '{db}'; tables: {self.tables(db)}")
            return db
        owners = [label for label, tables in self._columns.items() if table in tables]
        if not owners:
            known = sorted({t for tables in self._columns.values() for t in tables})
            raise LookupError(f"unknown table '{table}'; known tables: {known}")
        if len(owners) > 1:
            raise LookupError(f"table '{table}' exists in several dbs {owners}; say which with 'db'")
        return owners[0]

    # --- lifecycle -----------------------------------------------------
    def close(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
