"""The company handbook must explain WHERE the truth is, never contain the truth.

Seen in a real run: example values written into a handbook section ("lines such as Amount Due: ...") were copied by the
model into a bill for a different vendor, and made earlier results meaningless because the agent could pass by copying
the handbook instead of reading the document. This test fails if any text the model is given from company_pack/
contains a real value from the seed data (invoice numbers, amounts, dates, vendor names, form values).
"""
from pathlib import Path

import pytest

from apps.company import world

PACK = Path(__file__).resolve().parents[1] / "company_pack"
TEXT_FILES = sorted(p for p in PACK.rglob("*") if p.is_file() and p.suffix in (".md", ".yaml"))


def dmy(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}/{month}/{year}"


def answers() -> set[str]:
    found = set()
    for inv in [*world.INVOICES, world.DUPLICATE_INVOICE]:
        found |= {inv.invoice_no, dmy(inv.invoice_date), dmy(inv.due_date), f"{inv.amount:,.2f}", f"{inv.amount:,.0f}", f"{inv.amount:.2f}"}
    for vendor in [*world.VENDORS, world.DUPLICATE_VENDOR]:
        found |= {vendor.name, vendor.gst_no, vendor.email}
    for _, value in world.GLOBEX_FORM:
        found.add(value)
    # short values (a city, "45") are too common in prose to be a useful signal
    return {a for a in found if len(a) >= 6}


def test_the_list_of_answers_is_not_empty_so_the_test_can_fail():
    values = answers()
    assert "INV-2041" in values and "48,200.00" in values and "03/11/2026" in values and "Zenith Industrial Ltd" in values


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.relative_to(PACK).as_posix())
def test_no_real_value_appears_in_company_pack_text(path):
    text = path.read_text(encoding="utf-8")
    leaked = sorted(a for a in answers() if a in text)
    assert not leaked, f"{path.name} contains real values from the seed data: {leaked}"
