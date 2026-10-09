"""Small helpers shared by the app tests."""
import re
import sqlite3

from apps.company.db import TABLES


def query(client, sql, params=()):
    """Read the test app's SQLite file directly (tests may; the agent may not)."""
    conn = sqlite3.connect(client.app.state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def snapshot(client):
    """Every row of every table, so a test can prove 'nothing else changed'."""
    return {t: query(client, f"SELECT * FROM {t} ORDER BY rowid") for t in TABLES}


def buttons(html):
    """The <button ...>label</button> elements of a page, as raw strings."""
    return re.findall(r"<button[^>]*>.*?</button>", html, re.S)
