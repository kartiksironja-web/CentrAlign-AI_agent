"""Small helpers shared by the route modules: DB per request, HTML rendering.

Purpose: keep route code short and identical in how it reaches the DB and
renders templates.
Why this design: everything the routes need hangs off `app.state`
(db_path, faults, templates, admin_token), set once in create_app().
How it can fail: `get_db` opens a fresh connection per request, so a route
that forgets conn.commit() silently loses its write.
"""
from fastapi import Request

from apps.company.db import connect

# The ONLY banner texts. A banner is chosen by key from the query string, so a
# crafted URL can never inject text into the page.
FLASH = {
    "bill_saved": "Bill saved",
    "bill_updated": "Bill updated",
    "bill_paid": "Bill marked as paid",
    "vendor_saved": "Vendor saved",
}


def get_db(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def render(request: Request, template: str, status_code: int = 200, **context):
    context.setdefault("flash", FLASH.get(request.query_params.get("flash", "")))
    context["faults"] = request.app.state.faults
    return request.app.state.templates.TemplateResponse(request, template, context, status_code=status_code)
