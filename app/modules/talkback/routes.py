"""Talkback admin routes (#93). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audio, audit, audiobus, config, languages, transcribe
from .. import require
from .service import Refused, clean_settings, service

router = APIRouter()
VIEW = [Depends(require("talkback.view"))]
CONTROL = [Depends(require("talkback.control"))]
MANAGE = [Depends(require("talkback.manage"))]


def _state() -> dict:
    cfg = config.get()
    bus = cfg.get("audio_bus") or {}
    lanes = [{"lane": x["lane"], "name": x.get("name") or ""} for x in bus.get("lanes") or []]
    return {
        "settings": clean_settings(cfg.get("talkback")),
        "status": service.status(),
        "lines": service.recent(50),
        "lanes": sorted(lanes, key=lambda x: x["lane"]),
        "max_lane": audio.MAX_LANES,
        "bus_running": audiobus.bus.running,
        "backends": [{"id": b.id, "label": b.label, "available": b.available(cfg)} for b in transcribe.backends()],
        "main_backend": (cfg.get("realtime") or {}).get("backend") or transcribe.DEFAULT_BACKEND,
        "languages": [{"id": k, "label": v["label"]} for k, v in languages.SOURCE_LANGUAGES.items()],
        "source_language": cfg["source_language"],
    }


@router.get("/api/admin/talkback", dependencies=VIEW)
def admin_state():
    return _state()


@router.put("/api/admin/talkback/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    s = clean_settings(payload)
    if payload.get("backend") and not s["backend"]:
        return JSONResponse({"error": "Pick a transcription backend from the list."}, status_code=400)
    config.save({"talkback": s})
    if service.running:
        try:
            await service.restart()
        except Refused as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
    audit.note(request, "talkback.settings", "Changed the talkback settings",
               target=f"lane {s['lane']}" if s["lane"] else "no lane")
    return _state()


async def _run(request: Request, action: str, what: str):
    try:
        await getattr(service, action)()
    except Refused as exc:
        return JSONResponse({"error": str(exc), "key": exc.key}, status_code=409)
    audit.note(request, f"talkback.{action}", what)
    return _state()


@router.post("/api/admin/talkback/start", dependencies=CONTROL)
async def admin_start(request: Request):
    return await _run(request, "start", "Started talkback transcription")


@router.post("/api/admin/talkback/stop", dependencies=CONTROL)
async def admin_stop(request: Request):
    return await _run(request, "stop", "Stopped talkback transcription")


@router.post("/api/admin/talkback/restart", dependencies=CONTROL)
async def admin_restart(request: Request):
    return await _run(request, "restart", "Restarted talkback transcription")
