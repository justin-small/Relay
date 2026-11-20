"""Dante admin routes (#257). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audit
from .. import require
from .monitor import monitor
from .service import service

router = APIRouter()
VIEW = [Depends(require("dante.view"))]
MANAGE = [Depends(require("dante.manage"))]


@router.get("/api/admin/dante", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.post("/api/admin/dante/scan", dependencies=MANAGE)
async def admin_scan(request: Request):
    await service.rescan()
    audit.note(request, "dante.scan", "Looked for Dante devices again")
    return service.snapshot()


@router.get("/api/admin/dante/history", dependencies=VIEW)
def admin_history(limit: int = 100):
    return {"history": monitor.history(limit)}


@router.post("/api/admin/dante/star", dependencies=MANAGE)
async def admin_star(request: Request):
    """Star or unstar a transmit channel for live levels: {"device", "channel", "on"}."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    device, channel, on = body.get("device"), body.get("channel"), body.get("on")
    if not (isinstance(device, str) and isinstance(channel, str) and isinstance(on, bool)
            and 0 < len(device) <= 80 and 0 < len(channel) <= 80):
        raise HTTPException(400, "Send the device, the channel and on true or false.")
    known = {(d["name"], c) for d in service.snapshot()["devices"] for c in d["transmit"]}
    if (device, channel) not in known:
        raise HTTPException(404, "Relay has not seen that channel.")
    monitor.star(device, channel, on)
    audit.note(request, "dante.star", f"{'Starred' if on else 'Unstarred'} {channel} on {device} for live levels")
    return {"starred": sorted([d, c] for d, c in monitor.starred())}
