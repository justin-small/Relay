"""NDI admin routes (#294). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from ... import audit, config
from .. import require
from . import viewer
from .service import KEY, VIEW_LIMITS, clean_ip, clean_server, clean_settings, service

router = APIRouter()
VIEW = [Depends(require("ndi.view"))]
MANAGE = [Depends(require("ndi.manage"))]


VIEW_NAMES = {"view_fps": "Frames per second", "view_quality": "Picture quality", "view_width": "The widest picture",
              "view_max": "Sources watched at once"}


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
    servers = payload.get("servers", cur["servers"])
    if not isinstance(servers, list) or any(not clean_server(v) for v in servers):
        return _bad("A discovery server is an address like 192.168.1.20 or 192.168.1.20:5959.")
    if "mdns" in payload and not isinstance(payload["mdns"], bool):
        return _bad("Multicast DNS is on or off.")
    for key, (lo, hi, _) in VIEW_LIMITS.items():
        if key in payload and (isinstance(payload[key], bool) or not isinstance(payload[key], int) or not lo <= payload[key] <= hi):
            return _bad(f"{VIEW_NAMES[key]} is a whole number from {lo} to {hi}.")
    if "tally" in payload:
        t = payload["tally"]
        if not isinstance(t, dict) or any(not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 999
                                          for n in t.values()):
            return _bad("A camera number is a whole number from 1 to 999.")
    keys = ("servers", "mdns", "extra_ips", "groups", "interval", "tally", *VIEW_LIMITS)
    new = clean_settings({**cur, **{k: payload[k] for k in keys if k in payload}})
    if not new["mdns"] and not new["servers"] and not new["extra_ips"]:
        return _bad("With multicast DNS off, add a discovery server or an extra address, or nothing can be found.")
    config.save({KEY: new})
    audit.note(request, "ndi.settings", "Changed the NDI search settings")
    if service.running:
        await service.scan()
    return service.snapshot()


# ------------------------------------------------------------------------------------------------ viewer (#296)
# Served on the operator port only (the module's panel_prefixes), behind ndi.view, so the stream stays on the LAN.
# A path names a source that is on the list now: nothing here opens a receiver for a name Relay has not seen.
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _known(source: str) -> dict | None:
    return next((s for s in service.sources() if s["name"] == source and s["online"]), None)


@router.get("/ndi/view/{source:path}", response_class=HTMLResponse)
async def view_page(source: str, request: Request):
    from .. import page_gate, templates

    got = await asyncio.to_thread(page_gate, request, "ndi.view")
    if isinstance(got, Response):
        return got
    src = _known(source)
    if not service.running or src is None:
        return HTMLResponse("That NDI source is not available.", status_code=404, headers=NO_STORE)
    cam = service.settings()["tally"].get(source, 0)
    return templates.TemplateResponse(request, "ndi/view.html", {
        "data": {"source": source, "title": src["source"] or src["name"], "machine": src["machine"], "camera": cam}},
        headers=NO_STORE)


@router.get("/ndi/stream/{source:path}", dependencies=VIEW)
async def view_stream(source: str):
    if _known(source) is None:
        return JSONResponse({"error": "That NDI source is not available."}, status_code=404)
    feed = await asyncio.to_thread(service.viewers.open, source)
    if feed is None:
        return JSONResponse({"error": "Too many sources are being watched, or this one cannot be received. "
                                      "Try again in a moment."}, status_code=503)

    async def body():
        try:
            async for part in viewer.mjpeg(feed):
                yield part
        finally:
            await asyncio.to_thread(service.viewers.close, feed)

    return StreamingResponse(body(), media_type=f"multipart/x-mixed-replace; boundary={viewer.BOUNDARY}",
                             headers={**NO_STORE, "X-Accel-Buffering": "no"})


@router.get("/ndi/level/{source:path}", dependencies=VIEW)
async def view_level(source: str):
    """The audio peak in dBFS (null when there is none) and the frame size, for the viewer's meter and caption."""
    src = _known(source)
    if src is None:
        return JSONResponse({"error": "That NDI source is not available."}, status_code=404)
    return JSONResponse({"db": service.audio_level(source), "width": src["width"], "height": src["height"],
                         "fps": src["fps"]}, headers=NO_STORE)
