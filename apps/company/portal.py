"""Vendor invoice portal: the 'outside world' the agent reads from.

Purpose: lists vendors and their invoices, shows an invoice's number, date and
vendor, and serves the invoice PDF.
Why this design: the amount and due date are NOT on any web page; they exist
only in the PDF, so the agent must read a document. The invoice list is
deliberately unsorted (insertion order, see world.py) so "latest" has to be
worked out from the dates, not assumed from list position.
How it can fail: PDFs are located through the DB (pdf_path of a known invoice),
never by joining the URL onto a folder, so a crafted URL cannot read other files.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from apps.company.deps import get_db, render

router = APIRouter(prefix="/portal")


@router.get("/vendors")
def vendors(request: Request, conn=Depends(get_db)):
    rows = conn.execute("SELECT * FROM portal_vendors ORDER BY id").fetchall()
    return render(request, "portal_vendors.html", section="portal", vendors=rows)


@router.get("/invoices")
def invoices(request: Request, vendor_id: int | None = None, conn=Depends(get_db)):
    sql = (
        "SELECT i.invoice_no, i.invoice_date, v.name AS vendor_name, v.id AS vendor_id"
        " FROM portal_invoices i JOIN portal_vendors v ON v.id = i.vendor_id"
    )
    params: tuple = ()
    if vendor_id is not None:
        sql += " WHERE i.vendor_id = ?"
        params = (vendor_id,)
    rows = conn.execute(sql + " ORDER BY i.rowid", params).fetchall()  # rowid = insertion = unsorted
    return render(request, "portal_invoices.html", section="portal", invoices=rows)


def _invoice_or_404(conn, invoice_no: str):
    row = conn.execute(
        "SELECT i.*, v.name AS vendor_name FROM portal_invoices i"
        " JOIN portal_vendors v ON v.id = i.vendor_id WHERE i.invoice_no = ?",
        (invoice_no,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Invoice {invoice_no} not found")
    return row


@router.get("/invoices/{invoice_no}")
def invoice_detail(invoice_no: str, request: Request, conn=Depends(get_db)):
    return render(request, "portal_invoice.html", section="portal", invoice=_invoice_or_404(conn, invoice_no))


@router.get("/files/{filename}")
def invoice_file(filename: str, request: Request, conn=Depends(get_db)):
    if not filename.endswith(".pdf"):
        raise HTTPException(404, "Not found")
    invoice = _invoice_or_404(conn, filename[: -len(".pdf")])
    return FileResponse(request.app.state.data_dir / invoice["pdf_path"], media_type="application/pdf")
