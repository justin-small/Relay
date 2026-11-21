"""AES67 admin routes (#266). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audio, audit, config
from .. import require
from .output import Invalid, output, validate
from .service import MAX_REMEMBERED, clean_settings, service
from .source import SOURCE_ID

router = APIRouter()
VIEW = [Depends(require("aes67.view"))]
MANAGE = [Depends(require("aes67.manage"))]


def _state() -> dict:
    cfg = config.get()
    snap = service.snapshot()
    selected = cfg.get("audio_device") if cfg.get("audio_source") == SOURCE_ID else None
    src = audio.get_source(SOURCE_ID) if selected else None
    return {**snap, "selected": selected, "channel": cfg.get("input_channel", 0) if selected else None,
            "receiving": src.stats() if src is not None and hasattr(src, "stats") else None,
            "output": output.snapshot()}


@router.get("/api/admin/aes67", dependencies=VIEW)
def admin_state():
    return _state()


@router.post("/api/admin/aes67/remember", dependencies=MANAGE)
async def admin_remember(request: Request):
    """Keep a stream's SDP so the input that names it opens after a restart: {"id": "<group>:<port>"}. The page then
    saves the input through core's own settings (audio_source "aes67", audio_device the id)."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    sid = body.get("id") if isinstance(body, dict) else None
    if not isinstance(sid, str) or not 0 < len(sid) <= 40:
        raise HTTPException(400, "Send the id of the stream to keep.")
    stream, sdp = service.find(sid), service.sdp_of(sid)
    if stream is None:
        raise HTTPException(404, "Relay has not heard that stream announced.")
    if not stream.usable:
        raise HTTPException(409, "That stream cannot be received: " + stream.problem)
    kept = clean_settings(config.get().get("aes67"))["remembered"]
    if sdp is not None:
        kept.pop(sid, None)
        kept = dict(list(kept.items())[-(MAX_REMEMBERED - 1):])
        kept[sid] = sdp
    config.save({"aes67": {"remembered": kept}})
    service.configure({"remembered": kept})
    audit.note(request, "aes67.remember", f"Chose AES67 stream {stream.name}", target=sid)
    return {"id": sid, "name": stream.name}


@router.post("/api/admin/aes67/output", dependencies=MANAGE)
async def admin_output(request: Request):
    """Save the stream Relay sends and apply it: name, address, port, channels, format, gain and label per channel, the
    channel the alert tone is on and the channel each spoken language is on. The stream starts or stops to match."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    try:
        settings = validate(body)
    except Invalid as exc:
        raise HTTPException(400, exc.message) from None
    keep = clean_settings(config.get().get("aes67"))["remembered"]
    config.save({"aes67": {"remembered": keep, "output": settings}}, replace=("aes67",))
    await asyncio.to_thread(output.configure, settings)
    audit.note(request, "aes67.output", "Changed the AES67 output" + (" (on)" if settings["enabled"] else " (off)"))
    return output.snapshot()


@router.post("/api/admin/aes67/output/test", dependencies=MANAGE)
async def admin_output_test(request: Request):
    """A 1 kHz test tone for two seconds on one channel: {"channel": 1}."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    ch = body.get("channel") if isinstance(body, dict) else None
    if isinstance(ch, bool) or not isinstance(ch, int) or not 1 <= ch <= output.settings["channels"]:
        raise HTTPException(400, "Send the number of a channel inside the stream.")
    if not output.test_tone(ch):
        raise HTTPException(409, "The AES67 output is not running. Switch it on and save first.")
    return {"channel": ch}
