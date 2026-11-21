"""NDI admin routes (#294). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from ... import audit, config, schedules
from .. import require
from . import bridge, listen, multiview, outputs, testpatterns, viewer, watch
from .service import KEY, LOW, VIEW_LIMITS, clean_ip, clean_server, clean_settings, service

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
    if "watch" in payload:
        w = payload["watch"]
        if not isinstance(w, dict) or any(k in w and not isinstance(w[k], bool) for k in ("on", "auto", "sound")):
            return _bad("Watching a source is on or off.")
        if "hold" in w and (isinstance(w["hold"], bool) or not isinstance(w["hold"], int)
                            or not watch.HOLD_MIN <= w["hold"] <= watch.HOLD_MAX):
            return _bad(f"The hold time is a whole number of seconds from {watch.HOLD_MIN} to {watch.HOLD_MAX}.")
        if "sources" in w and (not isinstance(w["sources"], list) or len(w["sources"]) > watch.MAX_SOURCES
                               or any(not isinstance(n, str) for n in w["sources"])):
            return _bad(f"Watch at most {watch.MAX_SOURCES} sources, by name.")
        if "schedule" in w and (not isinstance(w["schedule"], str)
                                or (w["schedule"] and schedules.find(config.get()["schedules"], w["schedule"]) is None)):
            return _bad("That schedule does not exist.")
        payload = {**payload, "watch": {**cur["watch"], **w}}
    keys = ("servers", "mdns", "extra_ips", "groups", "interval", "tally", "multiviews", "watch", *VIEW_LIMITS)
    new = clean_settings({**cur, **{k: payload[k] for k in keys if k in payload}})
    if not new["mdns"] and not new["servers"] and not new["extra_ips"]:
        return _bad("With multicast DNS off, add a discovery server or an extra address, or nothing can be found.")
    config.save({KEY: new})
    audit.note(request, "ndi.settings", "Changed the NDI search settings")
    if service.running:
        await service.scan()
    return service.snapshot()


# ------------------------------------------------------------------------------------------------ outputs (#299)
@router.put("/api/admin/ndi/bridge", dependencies=MANAGE)
async def admin_bridge(request: Request, payload: dict):
    """The booth's NDI Bridge machines (a name, Host or Join, address and port). Relay only lists and checks them."""
    raw = payload.get("machines")
    if not isinstance(raw, list) or len(raw) > bridge.MAX_MACHINES:
        return _bad(f"Keep at most {bridge.MAX_MACHINES} Bridge machines.")
    clean = bridge.clean_machines(raw)
    if len(clean) != len(raw):
        return _bad("Give each machine its own name and an address like 203.0.113.5 or bridge.example.org, with a port from 1 to 65535.")
    service.save_settings({"bridge": clean})
    audit.note(request, "ndi.bridge", "Changed the NDI Bridge machines")
    if service.running:
        service.bridge_status = await asyncio.to_thread(bridge.check, clean)
    service._bump()
    return service.snapshot()


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


# ------------------------------------------------------------------------------------------------ test patterns (#302)
@router.put("/api/admin/ndi/tests", dependencies=MANAGE)
async def admin_tests(request: Request, payload: dict):
    """The test sources (a list; one without an id is new) and which are on."""
    cur = service.settings()
    raw = payload.get("tests", cur["tests"])
    if not isinstance(raw, list) or any(not isinstance(t, dict) for t in raw):
        return _bad("The test sources are a list.")
    if len(raw) > testpatterns.MAX_TESTS:
        return _bad(f"Keep at most {testpatterns.MAX_TESTS} test sources.")
    tests = [{**t, "id": t["id"] if isinstance(t.get("id"), str) and t["id"] else testpatterns.new_id()} for t in raw]
    clean = testpatterns.clean_tests(tests)
    if len(clean) != len(tests):
        return _bad("Give each test source its own name (letters, numbers, spaces), and use a size from the list.")
    on = payload.get("on", cur["tests_on"])
    if not isinstance(on, list) or any(v not in {t["id"] for t in clean} for v in on) or len(set(on)) > testpatterns.MAX_ON:
        return _bad(f"Switch on at most {testpatterns.MAX_ON} test sources at once.")
    names = {outputs.ndi_name(d["name"]).lower() for d in service.pages.defs()}
    if any(testpatterns.ndi_name(t).lower() in names for t in clean):
        return _bad("A page output already has that name.")
    for gone in {t["id"] for t in cur["tests"]} - {t["id"] for t in clean}:
        service.tests.forget(gone)
    service.save_settings({"tests": clean, "tests_on": on})
    audit.note(request, "ndi.tests", "Changed the NDI test patterns")
    if service.running:
        await asyncio.to_thread(service.tests.sync)
    return service.snapshot()


@router.put("/api/admin/ndi/tests/{tid}/image", dependencies=MANAGE)
async def admin_test_image(request: Request, tid: str):
    """The picture a test source of the "Your own picture" kind shows: the PNG is the request body."""
    if tid not in {t["id"] for t in service.settings()["tests"]}:
        return _bad("That test source does not exist.", 404)
    raw = await request.body()
    why = await asyncio.to_thread(testpatterns.check_image, raw)
    if why:
        return _bad(why)
    path = service.tests.image_path(tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, raw)
    audit.note(request, "ndi.tests", "Uploaded a picture for an NDI test pattern")
    if service.running:        # draw it again: the running source loaded the old picture when it started
        await asyncio.to_thread(service.tests.restart, tid)
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


@router.put("/api/admin/ndi/router/outputs/{oid}/audio", dependencies=MANAGE)
async def router_audio(oid: str, request: Request, payload: dict):
    """Take an output's sound from another source than its picture ("" for the picture's own)."""
    src = payload.get("source", "")
    if not isinstance(src, str):
        return _bad("A source is a name, or empty for the picture's own sound.")
    err = await asyncio.to_thread(service.router.set_audio, oid, src)
    return _router_done(request, "Set the sound of an NDI output", err)


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
    """The audio peak in dBFS (null when there is none), each channel's peak and RMS, the loudness, whether the source is
    audio only, and the frame size: for the viewer's meters and caption."""
    src = _known(source)
    if src is None:
        return JSONResponse({"error": "That NDI source is not available."}, status_code=404)
    return JSONResponse({"db": service.audio_level(source), "width": src["width"], "height": src["height"],
                         "fps": src["fps"], **service.audio_state(source)}, headers=NO_STORE)


_listeners = listen.Listeners()


@router.get("/ndi/listen/{source:path}", dependencies=VIEW)
async def view_listen(source: str, ch: str = ""):
    """The source's sound as endless 16-bit stereo PCM (little endian) for a browser to play; `ch=1,2` picks channels
    (counted from 1; none means all). The sample rate is in the `X-Rate` header and the source's channel count in
    `X-Channels`."""
    if _known(source) is None:
        return JSONResponse({"error": "That NDI source is not available."}, status_code=404)
    pick = listen.parse_channels(ch)
    if pick is None:
        return JSONResponse({"error": "Channels are numbers from 1, like 1,2."}, status_code=400)
    if not _listeners.take():
        return JSONResponse({"error": f"At most {listen.MAX_LISTENERS} people can listen at once. Try again later."},
                            status_code=503)
    stream = listen.Stream(asyncio.get_running_loop(), pick)
    stream.lease = await asyncio.to_thread(service.hook_listen, source, stream.feed)
    if stream.lease is None or not await stream.first():
        stream.close()
        _listeners.give()
        return JSONResponse({"error": "That source is not sending any sound."}, status_code=404)

    async def body():
        try:
            async for data in stream.chunks():
                yield data
        finally:
            await asyncio.to_thread(stream.close)
            _listeners.give()

    return StreamingResponse(body(), media_type="application/octet-stream",
                             headers={**NO_STORE, "X-Rate": str(stream.rate), "X-Channels": str(stream.channels),
                                      "X-Accel-Buffering": "no"})


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


# ------------------------------------------------------------------------------------------------ analysis (#300)
# One source at a time, from a receiver Relay holds for the admin page while it keeps asking (30 s after the last ask).
# `low` tests the sender's low-bandwidth stream.
def _akey(source: str, low: bool) -> str:
    return source + LOW if low else source


def _online(source) -> dict | None:
    if not isinstance(source, str):
        return None
    return next((s for s in service.sources() if s["name"] == source and s["online"]), None)


@router.post("/api/admin/ndi/analysis/open", dependencies=VIEW)
async def analysis_open(payload: dict):
    source, low = payload.get("source"), payload.get("low", False)
    if not isinstance(low, bool):
        return _bad("Low bandwidth is on or off.")
    if not service.running or _online(source) is None:
        return _bad("That NDI source is not available.", 404)
    key = await asyncio.to_thread(service.analysis_open, source, low)
    if key is None:
        return _bad("That source cannot be received.", 409)
    return service.analysis_snapshot(key)


@router.get("/api/admin/ndi/analysis", dependencies=VIEW)
async def analysis_read(source: str, low: bool = False, scopes: bool = True):
    """The newest numbers. Asking also keeps the analysis open. `scopes=false` leaves out the picture scopes."""
    key = _akey(source, low)
    cur = service._open.get(key)
    if cur is None:
        return _bad("That analysis is not open.", 404)
    await asyncio.to_thread(service.analysis_open, source, low)
    snap = await asyncio.to_thread(service.analysis_snapshot, key)
    if not scopes:
        snap.pop("picture", None)
    return JSONResponse(snap, headers=NO_STORE)


@router.delete("/api/admin/ndi/analysis", dependencies=VIEW)
async def analysis_close(source: str, low: bool = False):
    await asyncio.to_thread(service.analysis_close, _akey(source, low))
    return {"ok": True}


@router.post("/api/admin/ndi/analysis/reset", dependencies=VIEW)
async def analysis_reset(payload: dict):
    """Start the loudness and clip counts again."""
    t = service.analysis.tracks.get(_akey(str(payload.get("source", "")), bool(payload.get("low", False))))
    if t is None:
        return _bad("That analysis is not open.", 404)
    t.meter.reset_stats()
    return {"ok": True}


@router.post("/api/admin/ndi/analysis/record", dependencies=MANAGE)
async def analysis_record(request: Request, payload: dict):
    """Record per-frame timing to a CSV for `seconds` (1 to 3600). Needs the analysis open."""
    seconds = payload.get("seconds", 60)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 3600:
        return _bad("A recording is a whole number of seconds from 1 to 3600.")
    source = payload.get("source")
    if _online(source) is None:
        return _bad("That NDI source is not available.", 404)
    rec = service.analysis.record(_akey(source, bool(payload.get("low", False))), seconds)
    if rec is None:
        return _bad("Open the analysis first.", 409)
    audit.note(request, "ndi.analysis", "Recorded NDI frame timing")
    return rec.status()


@router.post("/api/admin/ndi/analysis/record/stop", dependencies=MANAGE)
async def analysis_record_stop(payload: dict):
    service.analysis.stop_recording(_akey(str(payload.get("source", "")), bool(payload.get("low", False))))
    return {"ok": True}


@router.get("/api/admin/ndi/analysis/recording", dependencies=VIEW)
async def analysis_download(source: str, low: bool = False):
    """The newest recording for the source, as a CSV to send to whoever runs the network."""
    rec = service.analysis.last_recording.get(_akey(source, low))
    if rec is None or not rec.path.exists():
        return _bad("There is no recording for that source yet.", 404)
    return FileResponse(rec.path, media_type="text/csv", filename=rec.path.name, headers=NO_STORE)
