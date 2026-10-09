"""The ERP's business rules, in one place.

Purpose: create a vendor, create a bill, edit a bill, mark a bill paid.
Why this design: the web forms (erp.py) and the JSON API (erp_api.py) both call
these functions, so the two paths cannot drift apart. That matters for the
agent: when the UI is broken it must be able to do exactly the same thing via
the API. Dates arrive here already converted to ISO; each caller owns its own
date parsing (the UI parses DD/MM/YYYY, the API wants YYYY-MM-DD).
These rules are deliberately lenient in one way: the ERP does NOT reject a
duplicate invoice number. Checking for an existing bill is the worker's job
(SOP rule), and the auditor catches a duplicate row.
How it can fail: ErpError(status, message) is the only error type; callers
turn it into an HTML error banner or a JSON error.
"""
import math
import sqlite3


class ErpError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


def _check_amount(amount) -> float:
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ErpError("Amount must be a number")
    if not math.isfinite(amount) or amount <= 0:
        raise ErpError("Amount must be greater than 0")
    return round(amount, 2)


def get_bill(conn: sqlite3.Connection, bill_id: int) -> dict:
    row = conn.execute("SELECT * FROM erp_bills WHERE id = ?", (bill_id,)).fetchone()
    if row is None:
        raise ErpError(f"Bill {bill_id} not found", 404)
    return dict(row)


def create_vendor(conn, name, city, gst_no, email, payment_terms_days) -> dict:
    name = (name or "").strip()
    if not name:
        raise ErpError("Vendor name is required")
    try:
        terms = int(payment_terms_days)
    except (TypeError, ValueError):
        raise ErpError("Payment terms must be a whole number of days")
    if terms < 0:
        raise ErpError("Payment terms cannot be negative")
    cur = conn.execute(
        "INSERT INTO erp_vendors (name, city, gst_no, email, payment_terms_days) VALUES (?, ?, ?, ?, ?)",
        (name, (city or "").strip(), (gst_no or "").strip(), (email or "").strip(), terms),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM erp_vendors WHERE id = ?", (cur.lastrowid,)).fetchone())


def create_bill(conn, vendor_id, invoice_no, amount, due_date_iso: str, via: str) -> dict:
    """`via` is set by the server ('ui' or 'api'); a client can never choose it."""
    invoice_no = (invoice_no or "").strip()
    if not invoice_no:
        raise ErpError("Invoice number is required")
    try:
        vendor_id = int(vendor_id)
    except (TypeError, ValueError):
        raise ErpError("Select a vendor")
    if conn.execute("SELECT 1 FROM erp_vendors WHERE id = ?", (vendor_id,)).fetchone() is None:
        raise ErpError(f"Vendor {vendor_id} does not exist")
    amount = _check_amount(amount)
    cur = conn.execute(
        "INSERT INTO erp_bills (vendor_id, invoice_no, amount, due_date, status, created_via)"
        " VALUES (?, ?, ?, ?, 'Open', ?)",
        (vendor_id, invoice_no, amount, due_date_iso, via),
    )
    conn.commit()
    return get_bill(conn, cur.lastrowid)


def update_bill(conn, bill_id: int, amount=None, due_date_iso: str | None = None, status: str | None = None) -> dict:
    """Partial update. Only fields that are not None change. Validate first, write once."""
    current = get_bill(conn, bill_id)
    new_amount = current["amount"] if amount is None else _check_amount(amount)
    new_due = current["due_date"] if due_date_iso is None else due_date_iso
    new_status = current["status"]
    if status == "Paid":
        if current["status"] != "Open":
            raise ErpError(f"Bill {bill_id} is {current['status']}, only Open bills can be marked paid", 409)
        new_status = "Paid"
    elif status is not None and status != current["status"]:
        raise ErpError("Status can only change from Open to Paid")
    conn.execute(
        "UPDATE erp_bills SET amount = ?, due_date = ?, status = ? WHERE id = ?",
        (new_amount, new_due, new_status, bill_id),
    )
    conn.commit()
    return get_bill(conn, bill_id)


def mark_paid(conn, bill_id: int) -> dict:
    return update_bill(conn, bill_id, status="Paid")
