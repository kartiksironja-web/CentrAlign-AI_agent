"""Internal ERP: the JSON API.

Purpose: everything the agent can do through the ERP web UI can also be done
here (list/create vendors, list/create bills, edit a bill, mark it paid via
status). It is the escape hatch when the UI misbehaves.
Why this design: thin wrappers over erp_logic.py, so UI and API apply identical
rules. Dates in the API are strict ISO (YYYY-MM-DD), unlike the UI's DD/MM/YYYY,
so the DATE_TRAP fault cannot affect it. `created_via` is set to 'api' by the
server; the request body cannot set it.
How it can fail: an invalid date gives 422 with a message naming the expected
format; unknown bill ids give 404; marking a non-Open bill paid gives 409.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from apps.company import dates, erp_logic
from apps.company.deps import get_db

router = APIRouter(prefix="/erp/api")


def _iso(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return dates.parse_iso(value)
    except ValueError:
        raise ValueError("due_date must be YYYY-MM-DD")


class VendorIn(BaseModel):
    name: str
    city: str = ""
    gst_no: str = ""
    email: str = ""
    payment_terms_days: int


class BillIn(BaseModel):
    vendor_id: int
    invoice_no: str
    amount: float
    due_date: str

    _check_date = field_validator("due_date")(_iso)


class BillUpdate(BaseModel):
    amount: float | None = None
    due_date: str | None = None
    status: str | None = None

    _check_date = field_validator("due_date")(_iso)


@router.get("/vendors")
def list_vendors(conn=Depends(get_db)):
    return [dict(r) for r in conn.execute("SELECT * FROM erp_vendors ORDER BY id")]


@router.post("/vendors", status_code=201)
def create_vendor(body: VendorIn, conn=Depends(get_db)):
    return erp_logic.create_vendor(conn, **body.model_dump())


@router.get("/bills")
def list_bills(vendor_id: int | None = None, invoice_no: str | None = None, conn=Depends(get_db)):
    sql, clauses, params = "SELECT * FROM erp_bills", [], []
    if vendor_id is not None:
        clauses.append("vendor_id = ?")
        params.append(vendor_id)
    if invoice_no is not None:
        clauses.append("invoice_no = ?")
        params.append(invoice_no)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]


@router.get("/bills/{bill_id}")
def get_bill(bill_id: int, conn=Depends(get_db)):
    return erp_logic.get_bill(conn, bill_id)


@router.post("/bills", status_code=201)
def create_bill(body: BillIn, conn=Depends(get_db)):
    return erp_logic.create_bill(conn, body.vendor_id, body.invoice_no, body.amount, body.due_date, via="api")


@router.put("/bills/{bill_id}")
def update_bill(bill_id: int, body: BillUpdate, conn=Depends(get_db)):
    return erp_logic.update_bill(conn, bill_id, body.amount, body.due_date, body.status)
