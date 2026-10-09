"""All data of the mock world, in one place.

Purpose: the single source of truth for what is in the portal, the ERP, and
the generated documents. seed.py writes it, faults.py adds/removes the
duplicate-vendor rows, and the tests assert against it.
Why this design: the data is fixed (no randomness) so docs and evals can cite
exact values. Vendor ids are identical in the portal and the ERP, because the
auditor compares vendor_id across the two databases.
How it can fail: if you change a value here, README/doc examples that quote it
(e.g. INV-2041 = INR 48,200.00, due 03/11/2026) become wrong.

Date traps built into the data: due dates on INV-2041, INV-3107 and
INV-2033-R1 have day <= 12, so reading them as MM/DD gives a *valid but wrong*
date. That is what makes the DATE_TRAP fault silent.
"""
from dataclasses import dataclass

OUR_COMPANY = "Nimbus Retail Pvt Ltd"  # who the invoices are billed to (fictional)


@dataclass(frozen=True)
class Vendor:
    id: int
    name: str
    city: str
    gst_no: str
    email: str
    payment_terms_days: int


@dataclass(frozen=True)
class Invoice:
    invoice_no: str
    vendor_id: int
    invoice_date: str  # ISO
    amount: float
    due_date: str  # ISO
    revised_of: str | None = None

    @property
    def pdf_path(self) -> str:
        """Relative to the data folder."""
        return f"portal_files/{self.invoice_no}.pdf"


@dataclass(frozen=True)
class Bill:
    vendor_id: int
    invoice_no: str
    amount: float
    due_date: str
    status: str
    created_via: str


# Vendors exist in both the portal and the ERP with the same ids. GST numbers
# and emails are fictional (".example" is a reserved non-real domain).
VENDORS = [
    Vendor(3, "Kaveri Packaging Co", "Chennai", "33AAKCK4821M1Z3", "accounts@kaveri-packaging.example", 30),
    Vendor(5, "Zenith Industrial Ltd", "Mumbai", "27AAGCZ7710P1Z8", "billing@zenith-industrial.example", 45),
    Vendor(7, "Acme Supplies Pvt Ltd", "Pune", "27AABCA5521D1Z2", "ar@acme-supplies.example", 30),
]

# DUPLICATE_VENDOR fault: a second, similarly named vendor (and a newer invoice,
# so picking the wrong vendor gives a different "latest invoice").
DUPLICATE_VENDOR = Vendor(8, "Acme Supplies LLC", "Dubai", "N/A", "ar@acme-supplies-llc.example", 30)
DUPLICATE_INVOICE = Invoice("INV-9001", 8, "2026-10-08", 12500.00, "2026-11-07")

# Portal invoices. The ORDER HERE IS THE ORDER THE PORTAL LISTS THEM: deliberately
# not sorted by date or number, and the newest of each vendor is never first or last.
INVOICES = [
    Invoice("INV-2039", 7, "2026-09-05", 36450.00, "2026-10-05"),
    Invoice("INV-3090", 5, "2026-09-04", 64000.00, "2026-10-04"),
    Invoice("INV-2033", 7, "2026-07-06", 24500.00, "2026-08-05"),
    Invoice("INV-5520", 3, "2026-09-18", 7850.00, "2026-10-18"),
    Invoice("INV-2041", 7, "2026-10-02", 48200.00, "2026-11-03"),  # newest Acme invoice
    Invoice("INV-3107", 5, "2026-10-05", 80000.00, "2026-11-10"),  # newest Zenith invoice; > 50,000
    Invoice("INV-3101", 5, "2026-09-22", 41000.00, "2026-10-22"),
    # Revised document for INV-2033: same invoice date, new due date. Dated before
    # INV-2038 so it can never compete for "latest invoice".
    Invoice("INV-2033-R1", 7, "2026-07-06", 24500.00, "2026-09-07", revised_of="INV-2033"),
    Invoice("INV-2038", 7, "2026-08-04", 29990.00, "2026-09-03"),
]

# What the invoice documents say was supplied (flavour text for the PDFs).
DESCRIPTIONS = {
    3: "Corrugated packaging cartons",
    5: "Machine tool spares",
    7: "Industrial consumables, assorted",
    8: "Industrial consumables, assorted",
}

# ERP bills that already exist. INV-2033 carries the OLD due date (task: update it
# from the revised invoice). INV-2039 is Open (task: mark it paid).
BILLS = [
    Bill(7, "INV-2033", 24500.00, "2026-08-05", "Open", "seed"),
    Bill(7, "INV-2039", 36450.00, "2026-10-05", "Open", "seed"),
]

# Labeled lines of inputs/new_vendor_form_globex.pdf (task: onboard this vendor).
# Globex is NOT in the ERP. Payment terms 45 <= 60 so onboarding needs no approval.
GLOBEX_FORM = [
    ("Company Name", "Globex Components Pvt Ltd"),
    ("City", "Bengaluru"),
    ("GST No", "29AABCG1234F1Z7"),
    ("Contact Email", "accounts@globex-components.example"),
    ("Payment Terms (days)", "45"),
]
