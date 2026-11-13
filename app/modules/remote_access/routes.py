"""Remote access's admin routes. Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .. import require
from . import cloud
from .service import enrolled, service, settings, url
from .tunnel import find_binary, tunnel

log = logging.getLogger("relay.remote_access")

router = APIRouter()

# What the page says about the tunnel, by state. Fixed sentences: nothing from cloudflared's own output.
TUNNEL_TEXT = {
    "off": "The tunnel is not running.",
    "missing": "cloudflared is missing from this install of Relay, so the tunnel can't run. Reinstall Relay.",
    "connecting": "Connecting to Cloudflare…",
    "connected": "Connected. Viewers anywhere can open the link.",
    "retrying": "Lost the connection to Cloudflare. Trying again…",
}


def view() -> dict:
    s = settings()
    return {
        "enrolled": enrolled(s),
        "url": url(s),
        "state": tunnel.state,
        "connections": tunnel.connections,
        "detail": TUNNEL_TEXT.get(tunnel.state, ""),
        "cloud_error": cloud.MESSAGES.get(service.cloud_error, "") if service.cloud_error else "",
        "cloudflared": bool(find_binary()),
    }


@router.get("/api/admin/remote-access", dependencies=[Depends(require("remote_access.view"))])
async def remote_access_view():
    return view()


@router.post("/api/admin/remote-access/enroll", dependencies=[Depends(require("remote_access.manage"))])
async def remote_access_enroll(payload: dict):
    code = str(payload.get("code") or "").strip()
    if not code or len(code) > 40:
        return JSONResponse({"error": "Enter the enrollment code you were given."}, status_code=400)
    if enrolled():
        return JSONResponse({"error": "This install already has an address. Release it first to enroll again."},
                            status_code=409)
    try:
        await service.enroll(code)
    except cloud.CloudError as exc:
        log.warning("Remote access: enroll refused (%s)", exc.kind)
        return JSONResponse({"error": exc.message}, status_code=400 if exc.status in (403, 409) else 502)
    return view()


@router.post("/api/admin/remote-access/release", dependencies=[Depends(require("remote_access.manage"))])
async def remote_access_release():
    try:
        await service.release()
    except cloud.CloudError as exc:
        log.warning("Remote access: release failed (%s)", exc.kind)
        return JSONResponse({"error": exc.message}, status_code=502)
    return view()
