"""ProdMesh RTA admin routes (#149). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .client import clean_settings, service

router = APIRouter()
VIEW = [Depends(require("prodmesh_rta.view"))]
MANAGE = [Depends(require("prodmesh_rta.manage"))]


@router.get("/api/admin/prodmesh-rta", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.put("/api/admin/prodmesh-rta/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    url = payload.get("url", "")
    s = clean_settings(payload)
    if not isinstance(url, str) or (url.strip() and not s["url"]):
        return JSONResponse({"error": "Enter the analyser's address as ws://host:port/path (or wss://)."},
                            status_code=400)
    config.save({"prodmesh_rta": s})
    await service.restart()
    audit.note(request, "prodmesh_rta.settings", "Changed the ProdMesh analyser's address", target=s["url"] or "(none)")
    return service.snapshot()
