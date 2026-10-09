"""The vendor portal: what is visible on pages vs only inside the PDF."""
import re

from apps.company.world import INVOICES


def listed_invoice_numbers(html):
    return re.findall(r'href="/portal/invoices/([^"]+)"', html)


def test_vendor_list(client):
    html = client.get("/portal/vendors").text
    assert "Acme Supplies Pvt Ltd" in html and "Zenith Industrial Ltd" in html
    assert "Acme Supplies LLC" not in html  # only with DUPLICATE_VENDOR


def test_invoice_list_is_deliberately_unsorted(client):
    listed = listed_invoice_numbers(client.get("/portal/invoices?vendor_id=7").text)
    assert listed == ["INV-2039", "INV-2033", "INV-2041", "INV-2033-R1", "INV-2038"]
    by_date = [i.invoice_no for i in sorted((i for i in INVOICES if i.vendor_id == 7), key=lambda i: i.invoice_date)]
    assert listed != by_date and listed != by_date[::-1]
    assert listed != sorted(listed) and listed != sorted(listed, reverse=True)


def test_invoice_list_without_filter_shows_everything(client):
    assert len(listed_invoice_numbers(client.get("/portal/invoices").text)) == 9


def test_invoice_list_shows_dates_day_first(client):
    html = client.get("/portal/invoices?vendor_id=7").text
    assert "02/10/2026" in html  # INV-2041
    assert "2026-10-02" not in html


def test_invoice_page_shows_number_date_vendor_and_pdf_link_but_not_amount_or_due_date(client):
    html = client.get("/portal/invoices/INV-2041").text
    assert "INV-2041" in html and "02/10/2026" in html and "Acme Supplies Pvt Ltd" in html
    assert 'href="/portal/files/INV-2041.pdf"' in html and "Download PDF" in html
    for hidden in ("48,200", "48200", "03/11/2026", "2026-11-03"):
        assert hidden not in html


def test_pdf_download(client):
    r = client.get("/portal/files/INV-2041.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_unknown_invoice_is_an_html_404(client):
    r = client.get("/portal/invoices/INV-0000")
    assert r.status_code == 404 and "not found" in r.text.lower() and "<html" in r.text


def test_files_route_cannot_read_arbitrary_files(client):
    assert client.get("/portal/files/company.db").status_code == 404  # not .pdf
    assert client.get("/portal/files/nope.pdf").status_code == 404  # not a known invoice
    assert client.get("/portal/files/..%2Fcompany.db").status_code == 404
