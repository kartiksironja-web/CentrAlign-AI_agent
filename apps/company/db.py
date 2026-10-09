"""SQLite schema and connection helper for the mock company.

Purpose: ONE SQLite file holds the vendor portal tables (portal_*) and the
internal ERP tables (erp_*).
Why this design: a single file means the (Phase 2) auditor can snapshot and
read everything through one read-only connection. Default journal mode (no WAL)
is deliberate: a read-only connection can read it without needing to write any
side files.
How it can fail: connections are opened per request with check_same_thread off
because FastAPI may run a dependency and its endpoint on different worker
threads; never share one connection between concurrent requests.
"""
import sqlite3
from pathlib import Path

TABLES = ("portal_vendors", "portal_invoices", "erp_vendors", "erp_bills")

SCHEMA = """
CREATE TABLE portal_vendors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);
CREATE TABLE portal_invoices (
    invoice_no TEXT PRIMARY KEY,
    vendor_id INTEGER NOT NULL REFERENCES portal_vendors(id),
    invoice_date TEXT NOT NULL,
    amount REAL NOT NULL,
    due_date TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    revised_of TEXT
);
CREATE TABLE erp_vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT,
    gst_no TEXT,
    email TEXT,
    payment_terms_days INTEGER NOT NULL
);
CREATE TABLE erp_bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL REFERENCES erp_vendors(id),
    invoice_no TEXT NOT NULL,
    amount REAL NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Open', 'Paid')),
    created_via TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rebuild_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate every table. Used instead of deleting the file because
    on Windows a file that is still open elsewhere cannot be deleted."""
    conn.executescript(
        "".join(f"DROP TABLE IF EXISTS {t};" for t in reversed(TABLES)) + SCHEMA
    )
