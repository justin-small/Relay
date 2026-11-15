"""Tally Arbiter admin routes (#244). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .listener import clean_settings, service

router = APIRouter()
VIEW = [Depends(require("tally_arbiter.view"))]
MANAGE = [Depends(require("tally_arbiter.manage"))]


@router.get("/api/admin/tally-arbiter", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.put("/api/admin/tally-arbiter/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    host = payload.get("host", "")
    if not isinstance(host, str):
        return JSONResponse({"error": "Enter Tally Arbiter's address."}, status_code=400)
    s = clean_settings({"host": host, "port": payload.get("port", 4455)})
    if host.strip() and not s["host"]:
        return JSONResponse({"error": "That is not an address: use the computer's IP address or name."},
                            status_code=400)
    config.save({"tally_arbiter": s})
    await service.restart()
    audit.note(request, "tally_arbiter.settings", "Changed Tally Arbiter's address", target=s["host"] or "(none)")
    return service.snapshot()
