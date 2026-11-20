"""Open Sound Meter admin routes (#149). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .listener import clean_settings, service

router = APIRouter()
VIEW = [Depends(require("open_sound_meter.view"))]
MANAGE = [Depends(require("open_sound_meter.manage"))]


@router.get("/api/admin/open-sound-meter", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.put("/api/admin/open-sound-meter/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    group = payload.get("group", "")
    if not isinstance(group, str):
        return JSONResponse({"error": "Enter a multicast address such as 239.1.1.1, or leave it empty."},
                            status_code=400)
    s = clean_settings(payload)
    if group.strip() and not s["group"]:
        return JSONResponse({"error": "That is not a multicast address. Use one from 224.0.0.0 to 239.255.255.255, "
                                      "or leave it empty to listen for plain UDP."}, status_code=400)
    config.save({"open_sound_meter": s})
    await service.restart()
    audit.note(request, "open_sound_meter.settings", "Changed where Open Sound Meter is heard",
               target=f"{s['group'] or 'UDP'}:{s['port']}")
    return service.snapshot()
