"""Creates the seeded mock company: SQLite tables, invoice PDFs, vendor form.

Purpose: `python -m apps.company.seed --reset` puts the world back to its
known starting state. The eval runner and /admin/reset call seed_database().
Why this design: the content comes from world.py (no randomness), so every
reset gives byte-identical rows. The invoice PDFs for the DUPLICATE_VENDOR
invoice are always generated, but its DB rows only exist while that fault is on.
How it can fail: it rebuilds all tables, so any bill created since the last
reset is lost (that is the point). Without --reset it refuses to touch an
existing database.
"""
import argparse
import sys
from pathlib import Path

from apps.company import config
from apps.company.db import connect, rebuild_schema
from apps.company.pdfs import write_invoice_pdf, write_vendor_form_pdf
from apps.company.world import (
    BILLS, DUPLICATE_INVOICE, DUPLICATE_VENDOR, GLOBEX_FORM, INVOICES, VENDORS,
)

VENDOR_FORM_NAME = "new_vendor_form_globex.pdf"


def seed_database(data_dir: Path, inputs_dir: Path | None = None) -> None:
    """(Re)build company.db and the portal PDFs under data_dir.

    If inputs_dir is given, also write the vendor onboarding form there.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir / "company.db")
    try:
        rebuild_schema(conn)
        for v in VENDORS:
            conn.execute(
                "INSERT INTO portal_vendors (id, name, city) VALUES (?, ?, ?)", (v.id, v.name, v.city)
            )
            conn.execute(
                "INSERT INTO erp_vendors (id, name, city, gst_no, email, payment_terms_days)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (v.id, v.name, v.city, v.gst_no, v.email, v.payment_terms_days),
            )
        for inv in INVOICES:  # insertion order = portal listing order (rowid)
            conn.execute(
                "INSERT INTO portal_invoices"
                " (invoice_no, vendor_id, invoice_date, amount, due_date, pdf_path, revised_of)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (inv.invoice_no, inv.vendor_id, inv.invoice_date, inv.amount, inv.due_date,
                 inv.pdf_path, inv.revised_of),
            )
        for b in BILLS:
            conn.execute(
                "INSERT INTO erp_bills (vendor_id, invoice_no, amount, due_date, status, created_via)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (b.vendor_id, b.invoice_no, b.amount, b.due_date, b.status, b.created_via),
            )
        conn.commit()
    finally:
        conn.close()

    vendors = {v.id: v for v in [*VENDORS, DUPLICATE_VENDOR]}
    for inv in [*INVOICES, DUPLICATE_INVOICE]:
        write_invoice_pdf(data_dir / inv.pdf_path, inv, vendors[inv.vendor_id])
    if inputs_dir is not None:
        write_vendor_form_pdf(inputs_dir / VENDOR_FORM_NAME, GLOBEX_FORM)


def main() -> int:
    config.load_env()
    parser = argparse.ArgumentParser(description="Create the seeded mock company database.")
    parser.add_argument("--reset", action="store_true", help="rebuild even if the database exists")
    parser.add_argument("--data-dir", type=Path, default=None, help="default: COMPANY_DATA_DIR or ./data")
    args = parser.parse_args()
    data_dir = args.data_dir or config.default_data_dir()
    if (data_dir / "company.db").exists() and not args.reset:
        print(f"{data_dir / 'company.db'} already exists; pass --reset to rebuild it.")
        return 1
    seed_database(data_dir, inputs_dir=config.INPUTS_DIR)
    print(f"Seeded {data_dir / 'company.db'} and wrote {config.INPUTS_DIR / VENDOR_FORM_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
