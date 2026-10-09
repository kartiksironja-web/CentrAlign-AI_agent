"""Generates the PDFs of the mock world (vendor invoices, a vendor form).

Purpose: the portal shows an invoice's number, date and vendor on the web page,
but the AMOUNT and DUE DATE exist only inside the PDF. The agent therefore has
to read a document, not just scrape HTML.
Why this design: fpdf2 is pure Python and writes one text line per cell, so
pdfplumber extracts clean "Label: value" lines. The rupee sign is not in the
built-in PDF fonts, so amounts are written as "INR 48,200.00".
Distractors are intentional: the invoice also lists subtotal, GST and the
invoice date, so the right line has to be picked by its label.
How it can fail: changing a label here breaks the (Phase 2) regex that parses
expected values out of the file.
"""
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from apps.company import dates
from apps.company.world import DESCRIPTIONS, OUR_COMPANY, Invoice, Vendor

GST_RATE = 0.18


def _new_pdf(title: str) -> FPDF:
    pdf = FPDF()
    pdf.set_creation_date(datetime(2026, 10, 1))  # fixed, so output does not vary by day
    pdf.set_title(title)
    pdf.add_page()
    return pdf


def _line(pdf: FPDF, text: str, size: int = 11, bold: bool = False) -> None:
    pdf.set_font("Helvetica", style="B" if bold else "", size=size)
    pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")


def _inr(amount: float) -> str:
    return f"INR {amount:,.2f}"


def write_invoice_pdf(path: Path, invoice: Invoice, vendor: Vendor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    title = "REVISED INVOICE" if invoice.revised_of else "INVOICE"
    pdf = _new_pdf(f"{title} {invoice.invoice_no}")
    subtotal = round(invoice.amount / (1 + GST_RATE), 2)
    gst = round(invoice.amount - subtotal, 2)

    _line(pdf, title, size=18, bold=True)
    pdf.ln(2)
    _line(pdf, f"Invoice No: {invoice.invoice_no}")
    _line(pdf, f"Invoice Date: {dates.to_dmy(invoice.invoice_date)}")
    if invoice.revised_of:
        _line(pdf, f"Revision of: {invoice.revised_of}. This document replaces the earlier one.")
    _line(pdf, f"Billed by: {vendor.name}, {vendor.city}")
    _line(pdf, f"GSTIN: {vendor.gst_no}")
    _line(pdf, f"Billed to: {OUR_COMPANY}")
    pdf.ln(2)
    _line(pdf, f"Description: {DESCRIPTIONS[vendor.id]}")
    _line(pdf, f"Subtotal: {_inr(subtotal)}")
    _line(pdf, f"GST (18%): {_inr(gst)}")
    _line(pdf, f"Amount Due: {_inr(invoice.amount)}", bold=True)
    _line(pdf, f"Due Date: {dates.to_dmy(invoice.due_date)}", bold=True)
    pdf.output(str(path))


def write_vendor_form_pdf(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = _new_pdf("Vendor onboarding form")
    _line(pdf, "VENDOR ONBOARDING FORM", size=18, bold=True)
    pdf.ln(2)
    for label, value in rows:
        _line(pdf, f"{label}: {value}")
    pdf.output(str(path))
