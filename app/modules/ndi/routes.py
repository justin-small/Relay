"""NDI admin routes (#294). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from ... import audit, config
from .. import require
from . import multiview, outputs, viewer
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
    if "multiviews" in payload and not isinstance(payload["multiviews"], list):
        return _bad("Multiviews are a list.")
    keys = ("servers", "mdns", "extra_ips", "groups", "interval", "tally", "multiviews", *VIEW_LIMITS)
    new = clean_settings({**cur, **{k: payload[k] for k in keys if k in payload}})
    if not new["mdns"] and not new["servers"] and not new["extra_ips"]:
        return _bad("With multicast DNS off, add a discovery server or an extra address, or nothing can be found.")
    config.save({KEY: new})
    audit.note(request, "ndi.settings", "Changed the NDI search settings")
    if service.running:
        await service.scan()
    return service.snapshot()


# ------------------------------------------------------------------------------------------------ outputs (#299)
@router.put("/api/admin/ndi/outputs", dependencies=MANAGE)
async def admin_outputs(request: Request, payload: dict):
    """Which page outputs are on, and their size and frame rate."""
    cur = service.settings()
    on = payload.get("on", cur["out_on"])
    if not isinstance(on, list) or any(not isinstance(v, str) for v in on) or len(set(on)) > outputs.MAX_ON:
        return _bad(f"Switch on at most {outputs.MAX_ON} outputs.")
    known = {d["id"] for d in service.pages.defs()}
    if any(v not in known for v in on):
        return _bad("One of those outputs does not exist.")
    if "size" in payload and payload["size"] not in outputs.SIZES:
        return _bad("The size is 1280x720 or 1920x1080.")
    fps = payload.get("fps", cur["out_fps"])
    if isinstance(fps, bool) or not isinstance(fps, int) or not 5 <= fps <= 30:
        return _bad("Frames per second is a whole number from 5 to 30.")
    service.save_settings({"out_on": on, "out_size": payload.get("size", cur["out_size"]), "out_fps": fps})
    audit.note(request, "ndi.outputs", "Changed which pages Relay sends as NDI")
    if service.running:
        await asyncio.to_thread(service.pages.sync)
    return service.snapshot()


# ------------------------------------------------------------------------------------------------ router (#298)
def _router_done(request: Request, what: str, err):
    if err:
        return _bad(err if isinstance(err, str) else "That did not work.", 409)
    audit.note(request, "ndi.router", what)
    return service.snapshot()


@router.post("/api/admin/ndi/router/outputs", dependencies=MANAGE)
async def router_add(request: Request, payload: dict):
    got = await asyncio.to_thread(service.router.add_output, payload.get("name"))
    return _router_done(request, "Added a routed NDI output", got if isinstance(got, str) else "")


@router.delete("/api/admin/ndi/router/outputs/{oid}", dependencies=MANAGE)
async def router_remove(oid: str, request: Request):
    ok = await asyncio.to_thread(service.router.remove_output, oid)
    return _router_done(request, "Removed a routed NDI output", "" if ok else "That output does not exist.")


@router.put("/api/admin/ndi/router/outputs/{oid}/source", dependencies=MANAGE)
async def router_route(oid: str, request: Request, payload: dict):
    src = payload.get("source", "")
    if not isinstance(src, str):
        return _bad("A source is a name, or empty for none.")
    err = await asyncio.to_thread(service.router.route, oid, src)
    return _router_done(request, "Routed an NDI output", err)


@router.post("/api/admin/ndi/router/salvos", dependencies=MANAGE)
async def router_salvo_save(request: Request, payload: dict):
    sid = payload.get("id", "")
    got = await asyncio.to_thread(service.router.save_salvo, payload.get("name"), sid if isinstance(sid, str) else "")
    return _router_done(request, "Saved an NDI preset", got if isinstance(got, str) else "")


@router.delete("/api/admin/ndi/router/salvos/{sid}", dependencies=MANAGE)
async def router_salvo_delete(sid: str, request: Request):
    ok = await asyncio.to_thread(service.router.delete_salvo, sid)
    return _router_done(request, "Deleted an NDI preset", "" if ok else "That preset does not exist.")


@router.post("/api/admin/ndi/router/salvos/{sid}/take", dependencies=MANAGE)
async def router_salvo_take(sid: str, request: Request):
    err = await asyncio.to_thread(service.router.take, sid)
    return _router_done(request, "Took an NDI preset", err)


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


# ------------------------------------------------------------------------------------------------ multiview (#297)
@router.get("/ndi/multiview/{mid}", response_class=HTMLResponse)
async def multiview_page(mid: str, request: Request):
    from .. import page_gate, templates

    got = await asyncio.to_thread(page_gate, request, "ndi.view")
    if isinstance(got, Response):
        return got
    mv = service.multiviews.get(mid)
    if not service.running or mv is None:
        return HTMLResponse("That multiview is not available.", status_code=404, headers=NO_STORE)
    return templates.TemplateResponse(request, "ndi/multiview.html", {"data": {"id": mv["id"], "title": mv["name"]}},
                                      headers=NO_STORE)


@router.get("/ndi/multiview/{mid}/stream", dependencies=VIEW)
async def multiview_stream(mid: str):
    comp = await asyncio.to_thread(service.multiviews.open, mid) if service.running else None
    if comp is None:
        return JSONResponse({"error": "That multiview is not available."}, status_code=404)

    async def body():
        try:
            async for part in viewer.mjpeg(comp):
                yield part
        finally:
            await asyncio.to_thread(service.multiviews.close, comp)

    return StreamingResponse(body(), media_type=f"multipart/x-mixed-replace; boundary={viewer.BOUNDARY}",
                             headers={**NO_STORE, "X-Accel-Buffering": "no"})
