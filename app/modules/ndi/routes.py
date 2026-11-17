"""NDI admin routes (#294). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .service import KEY, clean_ip, clean_settings, service

router = APIRouter()
VIEW = [Depends(require("ndi.view"))]
MANAGE = [Depends(require("ndi.manage"))]


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


@router.get("/api/admin/ndi", dependencies=VIEW)
async def admin_state(since: int = -1):
    """The sources. With `since=<version>` it waits (up to 20 s) for something to change: the page's live updates."""
    if since >= 0:
        await service.wait_change(since)
    return service.snapshot()


@router.post("/api/admin/ndi/scan", dependencies=MANAGE)
async def admin_scan(request: Request):
    if not service.running:
        return _bad(service.error or "NDI is not running.", 409)
    await service.scan()
    audit.note(request, "ndi.scan", "Scanned for NDI sources")
    return service.snapshot()


@router.put("/api/admin/ndi/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    cur = service.settings()
    ips = payload.get("extra_ips", cur["extra_ips"])
    if not isinstance(ips, list) or any(not clean_ip(v) for v in ips):
        return _bad("Extra addresses must be IP addresses, like 192.168.20.5.")
    new = clean_settings({**cur, **{k: payload[k] for k in ("extra_ips", "groups", "interval") if k in payload}})
    config.save({KEY: new})
    audit.note(request, "ndi.settings", "Changed the NDI search settings")
    if service.running:
        await service.scan()
    return service.snapshot()
