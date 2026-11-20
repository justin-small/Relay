"""AES67 admin routes (#266). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audio, audit, config
from .. import require
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
            "receiving": src.stats() if src is not None and hasattr(src, "stats") else None}


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
