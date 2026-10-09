"""Fault flags: switchable test conditions injected into the mock company.

Purpose: each fault recreates a real-world failure the agent must cope with.
They are OFF by default, are never mentioned to the agent, and are set by the
runner (env FAULTS at startup, or POST /admin/faults).
  DATE_TRAP         ERP reads typed due dates as MM/DD/YYYY, hides the format
                    hint, and still says "Bill saved"  (silent wrong data)
  SAVE_BROKEN       the new-bill Save button does nothing  (visible no-op)
  BUTTON_RENAME     that button's label and id change  (cosmetic)
  DUPLICATE_VENDOR  a second, similar vendor exists  (ambiguity)
Not built yet: SESSION_EXPIRY (P2, first on the cut list).
Why this design: three faults are pure rendering/parsing switches read from
FaultState at request time. Only DUPLICATE_VENDOR changes data, so it is applied
to the DB by sync_fault_rows() and always removed again when switched off.
How it can fail: if something already used id 8 (or INV-9001) before the fault
was switched on, sync_fault_rows raises FaultConflict instead of silently not
adding the duplicate. The runner always resets the DB first.
"""
import sqlite3

from apps.company.world import DUPLICATE_INVOICE, DUPLICATE_VENDOR

VALID_FAULTS = ("DATE_TRAP", "SAVE_BROKEN", "BUTTON_RENAME", "DUPLICATE_VENDOR")


class FaultConflict(Exception):
    pass


class FaultState:
    """The set of currently active faults, shared by every request."""

    def __init__(self, active=()):
        self._active: set[str] = set()
        self.set(active)

    def set(self, names) -> None:
        names = {n.strip() for n in names if n and n.strip()}
        unknown = names - set(VALID_FAULTS)
        if unknown:
            raise ValueError(f"unknown fault(s): {sorted(unknown)}; valid: {list(VALID_FAULTS)}")
        self._active = names

    def has(self, name: str) -> bool:
        return name in self._active

    @property
    def names(self) -> list[str]:
        return sorted(self._active)


def parse_fault_list(text: str | None) -> list[str]:
    """'DATE_TRAP, SAVE_BROKEN' -> ['DATE_TRAP', 'SAVE_BROKEN']; '' -> []."""
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def sync_fault_rows(conn: sqlite3.Connection, faults: FaultState) -> None:
    """Make the DB match the faults: add or remove the duplicate vendor rows."""
    if faults.has("DUPLICATE_VENDOR"):
        _add_duplicate(conn)
    else:
        _remove_duplicate(conn)
    conn.commit()


def _insert_once(conn, table: str, key_col: str, key, name_col: str, name: str, insert_sql: str, params) -> None:
    row = conn.execute(f"SELECT {name_col} FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    if row is None:
        conn.execute(insert_sql, params)
    elif row[0] != name:
        raise FaultConflict(f"{table}.{key_col}={key!r} is already used by {row[0]!r}; reset the DB first")


def _add_duplicate(conn: sqlite3.Connection) -> None:
    v, inv = DUPLICATE_VENDOR, DUPLICATE_INVOICE
    _insert_once(
        conn, "erp_vendors", "id", v.id, "name", v.name,
        "INSERT INTO erp_vendors (id, name, city, gst_no, email, payment_terms_days) VALUES (?, ?, ?, ?, ?, ?)",
        (v.id, v.name, v.city, v.gst_no, v.email, v.payment_terms_days),
    )
    _insert_once(
        conn, "portal_vendors", "id", v.id, "name", v.name,
        "INSERT INTO portal_vendors (id, name, city) VALUES (?, ?, ?)",
        (v.id, v.name, v.city),
    )
    _insert_once(
        conn, "portal_invoices", "invoice_no", inv.invoice_no, "invoice_no", inv.invoice_no,
        "INSERT INTO portal_invoices (invoice_no, vendor_id, invoice_date, amount, due_date, pdf_path, revised_of)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (inv.invoice_no, inv.vendor_id, inv.invoice_date, inv.amount, inv.due_date, inv.pdf_path, inv.revised_of),
    )


def _remove_duplicate(conn: sqlite3.Connection) -> None:
    v, inv = DUPLICATE_VENDOR, DUPLICATE_INVOICE
    conn.execute("DELETE FROM portal_invoices WHERE invoice_no = ?", (inv.invoice_no,))
    conn.execute("DELETE FROM portal_vendors WHERE id = ? AND name = ?", (v.id, v.name))
    conn.execute("DELETE FROM erp_vendors WHERE id = ? AND name = ?", (v.id, v.name))
