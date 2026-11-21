"""Wireless mics admin routes (#153). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audit, config, directory
from .. import require
from .service import Invalid, service, validate

router = APIRouter()
VIEW = [Depends(require("wireless_mics.view"))]
MANAGE = [Depends(require("wireless_mics.manage"))]


def _state() -> dict:
    out = service.snapshot()
    out["positions"] = [{"ref": p["ref"], "name": p["name"], "team": p["team"]} for p in directory.positions()]
    return out


@router.get("/api/admin/wireless-mics", dependencies=VIEW)
def admin_state():
    return _state()


@router.put("/api/admin/wireless-mics/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    try:
        s = validate(payload)
    except Invalid as exc:
        raise HTTPException(400, exc.message)
    config.save({"wireless_mics": s}, replace=("wireless_mics",))
    await service.restart()
    audit.note(request, "wireless_mics.settings", "Changed the wireless mics",
               target=f"{len(s['receivers'])} receivers, {len(s['manual'])} manual")
    return _state()
