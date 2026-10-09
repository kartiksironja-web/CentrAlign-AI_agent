"""Builds the mock-company FastAPI app (portal + ERP + admin) from explicit settings.

Purpose: create_app(data_dir, faults, admin_token) is the only way the app is
made, so tests, the eval runner and `python -m apps.company.main` all build it
the same way and each can point it at its own data folder.
Why this design: the settings live on `app.state`, not in module globals, so
several apps can exist in one process (tests) without sharing state. FastAPI's
auto docs (/docs, /openapi.json) are switched off: they would hand the agent a
map of every route, including /admin.
How it can fail: the data folder must already contain company.db (run the seed
first); create_app does not create it.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.templating import Jinja2Templates

from apps.company import admin, dates, erp, erp_api, portal
from apps.company.db import connect
from apps.company.deps import render
from apps.company.erp_logic import ErpError
from apps.company.faults import FaultState, sync_fault_rows

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(data_dir: Path, faults=(), admin_token: str | None = None) -> FastAPI:
    data_dir = Path(data_dir)
    app = FastAPI(title="Mock company", docs_url=None, redoc_url=None, openapi_url=None)

    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.filters["dmy"] = dates.to_dmy
    templates.env.filters["inr"] = lambda amount: f"₹{amount:,.2f}"

    app.state.data_dir = data_dir
    app.state.db_path = data_dir / "company.db"
    app.state.faults = FaultState(faults)
    app.state.admin_token = admin_token
    app.state.templates = templates

    conn = connect(app.state.db_path)
    try:
        sync_fault_rows(conn, app.state.faults)  # DUPLICATE_VENDOR rows present iff the fault is on
    finally:
        conn.close()

    for router in (portal.router, erp.router, erp_api.router, admin.router):
        app.include_router(router)

    @app.get("/")
    def index(request: Request):
        return render(request, "index.html", section=None)

    @app.exception_handler(ErpError)
    async def erp_error(request: Request, exc: ErpError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if path.startswith(("/erp/api/", "/admin/")):
            return await http_exception_handler(request, exc)  # JSON for machine endpoints
        section = "erp" if path.startswith("/erp") else "portal" if path.startswith("/portal") else None
        return render(request, "error.html", exc.status_code, section=section, message=str(exc.detail))

    return app
