"""Internal ERP: the server-rendered web UI (forms and lists).

Purpose: the pages a human clerk (or the browser tools of the agent) uses to
add vendors, add/edit bills and mark bills paid.
Why this design: plain HTML forms with a POST and a redirect, no JavaScript.
The business rules live in erp_logic.py (shared with the JSON API). Faults
are applied here at render/parse time:
  DATE_TRAP      due-date text is read as MM/DD/YYYY and the format hint is
                 hidden, but the same green "Bill saved" banner appears.
  SAVE_BROKEN    the new-bill button is type="button" and the form cannot be
                 submitted, so clicking does nothing (no request, no error).
  BUTTON_RENAME  the new-bill button reads "Submit Entry" with id submit-entry.
Faults touch the new-bill form (SAVE_BROKEN, BUTTON_RENAME) and due-date
parsing in the new-bill and edit forms (DATE_TRAP). The JSON API is unaffected.
How it can fail: under DATE_TRAP the edit form is pre-filled in DD/MM/YYYY but
parsed as MM/DD/YYYY, so even re-saving an untouched date corrupts it. That is
the trap behaving as designed.
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from apps.company import dates, erp_logic
from apps.company.deps import get_db, render
from apps.company.erp_logic import ErpError

router = APIRouter(prefix="/erp")


def _parse_ui_due_date(text: str, request: Request) -> str:
    """Typed due date -> ISO. DATE_TRAP flips DD/MM to MM/DD without telling anyone."""
    trap = request.app.state.faults.has("DATE_TRAP")
    try:
        return dates.parse_mdy(text) if trap else dates.parse_dmy(text)
    except ValueError:
        raise ErpError("Invalid due date" if trap else "Due date must be DD/MM/YYYY")


def _redirect(url: str):
    return RedirectResponse(url, status_code=303)  # 303: browser follows with GET after a POST


def _vendors(conn):
    return conn.execute("SELECT * FROM erp_vendors ORDER BY id").fetchall()


def _bills(conn):
    return conn.execute(
        "SELECT b.id, b.invoice_no, b.amount, b.due_date, b.status, v.name AS vendor_name, v.city AS vendor_city"
        " FROM erp_bills b JOIN erp_vendors v ON v.id = b.vendor_id ORDER BY b.id"
    ).fetchall()


@router.get("")
def erp_home():
    return _redirect("/erp/bills")


@router.get("/vendors")
def vendors(request: Request, conn=Depends(get_db)):
    return render(request, "erp_vendors.html", section="erp", vendors=_vendors(conn))


@router.get("/vendors/new")
def vendor_new_form(request: Request):
    return render(request, "erp_vendor_form.html", section="erp", values={})


@router.post("/vendors/new")
def vendor_new(
    request: Request,
    name: str = Form(""), city: str = Form(""), gst_no: str = Form(""),
    email: str = Form(""), payment_terms_days: str = Form(""),
    conn=Depends(get_db),
):
    values = dict(name=name, city=city, gst_no=gst_no, email=email, payment_terms_days=payment_terms_days)
    try:
        erp_logic.create_vendor(conn, **values)
    except ErpError as e:
        return render(request, "erp_vendor_form.html", e.status, section="erp", values=values, error=e.message)
    return _redirect("/erp/vendors?flash=vendor_saved")


@router.get("/bills")
def bills(request: Request, conn=Depends(get_db)):
    return render(request, "erp_bills.html", section="erp", bills=_bills(conn))


@router.get("/bills/new")
def bill_new_form(request: Request, conn=Depends(get_db)):
    return render(request, "erp_bill_form.html", section="erp", vendors=_vendors(conn), values={})


@router.post("/bills/new")
def bill_new(
    request: Request,
    vendor_id: str = Form(""), invoice_no: str = Form(""),
    amount: str = Form(""), due_date: str = Form(""),
    conn=Depends(get_db),
):
    values = dict(vendor_id=vendor_id, invoice_no=invoice_no, amount=amount, due_date=due_date)
    try:
        iso = _parse_ui_due_date(due_date, request)
        erp_logic.create_bill(conn, vendor_id, invoice_no, amount, iso, via="ui")
    except ErpError as e:
        return render(request, "erp_bill_form.html", e.status, section="erp",
                      vendors=_vendors(conn), values=values, error=e.message)
    return _redirect("/erp/bills?flash=bill_saved")


@router.get("/bills/{bill_id}/edit")
def bill_edit_form(bill_id: int, request: Request, conn=Depends(get_db)):
    try:
        bill = erp_logic.get_bill(conn, bill_id)
    except ErpError as e:
        return render(request, "error.html", e.status, section="erp", message=e.message)
    return _edit_page(request, conn, bill, {"amount": f"{bill['amount']:.2f}", "due_date": dates.to_dmy(bill["due_date"])})


def _edit_page(request, conn, bill, values, status_code=200, error=None):
    vendor = conn.execute("SELECT name, city FROM erp_vendors WHERE id = ?", (bill["vendor_id"],)).fetchone()
    return render(request, "erp_bill_edit.html", status_code, section="erp",
                  bill=bill, vendor=vendor, values=values, error=error)


@router.post("/bills/{bill_id}/edit")
def bill_edit(bill_id: int, request: Request, amount: str = Form(""), due_date: str = Form(""),
              conn=Depends(get_db)):
    try:
        bill = erp_logic.get_bill(conn, bill_id)
    except ErpError as e:
        return render(request, "error.html", e.status, section="erp", message=e.message)
    values = dict(amount=amount, due_date=due_date)
    try:
        iso = _parse_ui_due_date(due_date, request)
        erp_logic.update_bill(conn, bill_id, amount=amount, due_date_iso=iso)
    except ErpError as e:
        return _edit_page(request, conn, bill, values, e.status, e.message)
    return _redirect("/erp/bills?flash=bill_updated")


@router.post("/bills/{bill_id}/mark_paid")
def bill_mark_paid(bill_id: int, request: Request, conn=Depends(get_db)):
    try:
        erp_logic.mark_paid(conn, bill_id)
    except ErpError as e:
        return render(request, "erp_bills.html", e.status, section="erp", bills=_bills(conn), error=e.message)
    return _redirect("/erp/bills?flash=bill_paid")
