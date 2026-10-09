"""The only place the auditor builds SQL.

Purpose: select rows from a table by equality conditions, with an optional ORDER BY.
Why this design: assertions are data written by an LLM, so they are untrusted.
Table and column names are accepted ONLY if they exist in the schema that
ReadOnlyDbs read from the file (then double-quoted); every value is passed as
a bound parameter; ORDER BY must match `<field> [asc|desc]`. Nothing the agent
writes ever becomes raw SQL text.
How it can fail: unknown names raise LookupError with the list of valid names,
so the agent can correct itself. Only equality (AND) is supported on purpose;
richer conditions would need a richer, riskier language.
"""
import re

from auditor.readonly import ReadOnlyDbs

_ORDER = re.compile(r"\s*(\w+)(?:\s+(asc|desc))?\s*", re.IGNORECASE)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_order_by(text: str) -> tuple[str, str]:
    """'listed_on desc' -> ('listed_on', 'DESC'). Anything else is rejected."""
    m = _ORDER.fullmatch(text)
    if not m:
        raise LookupError(f"order_by must look like '<field> asc|desc', got {text!r}")
    return m.group(1), (m.group(2) or "asc").upper()


def check_fields(dbs: ReadOnlyDbs, db: str, table: str, fields) -> None:
    """Raise LookupError unless db, table and every field exist."""
    columns = dbs.columns(db, table)
    for f in fields:
        if f not in columns:
            raise LookupError(f"unknown field '{f}' in {db}.{table}; fields: {columns}")


def select_rows(dbs: ReadOnlyDbs, db: str, table: str, where=None, order_by=None, limit=None) -> list[dict]:
    where = where or {}
    check_fields(dbs, db, table, where)
    sql = f"SELECT * FROM {_q(table)}"
    params = list(where.values())
    if where:
        # IS behaves like = but also matches NULL when the value is None.
        sql += " WHERE " + " AND ".join(f"{_q(f)} IS ?" for f in where)
    if order_by:
        field, direction = parse_order_by(order_by)
        check_fields(dbs, db, table, [field])
        sql += f" ORDER BY {_q(field)} {direction}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in dbs.conn(db).execute(sql, params)]
