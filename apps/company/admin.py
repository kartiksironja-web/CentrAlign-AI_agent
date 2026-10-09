"""Admin endpoints: reset the world and switch faults. For the runner only.

Purpose: POST /admin/reset restores the seeded state; GET/POST /admin/faults
shows or sets the active fault flags.
Why this design: /admin lives on the same host and port as the ERP, so the
(Phase 3) policy gate denies it to the agent, AND every request needs an
X-Admin-Token header that only the runner knows (defence in depth: even if the
gate had a hole, the agent has no token). If ADMIN_TOKEN is not configured the
endpoints are disabled outright (503) rather than open.
How it can fail: faults are held in memory, so a server restart returns to the
FAULTS env value. Switching DUPLICATE_VENDOR on a database that already used
id 8 gives 409 (reset first).
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from apps.company.db import connect
from apps.company.faults import VALID_FAULTS, FaultConflict, sync_fault_rows
from apps.company.seed import seed_database


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.admin_token
    if not expected:
        raise HTTPException(503, "admin is disabled: ADMIN_TOKEN is not set")
    if not secrets.compare_digest(x_admin_token or "", expected):
        raise HTTPException(403, "bad or missing X-Admin-Token")


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


class FaultsIn(BaseModel):
    faults: list[str]


def _status(request: Request) -> dict:
    return {"active": request.app.state.faults.names, "valid": list(VALID_FAULTS)}


def _apply_faults(request: Request) -> None:
    conn = connect(request.app.state.db_path)
    try:
        sync_fault_rows(conn, request.app.state.faults)
    finally:
        conn.close()


@router.get("/faults")
def get_faults(request: Request):
    return _status(request)


@router.post("/faults")
def set_faults(body: FaultsIn, request: Request):
    faults = request.app.state.faults
    previous = faults.names
    try:
        faults.set(body.faults)
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        _apply_faults(request)
    except FaultConflict as e:
        faults.set(previous)
        raise HTTPException(409, str(e))
    return _status(request)


@router.post("/reset")
def reset(request: Request):
    seed_database(request.app.state.data_dir)
    _apply_faults(request)  # re-add duplicate rows if that fault is still on
    return {"status": "reset", **_status(request)}
