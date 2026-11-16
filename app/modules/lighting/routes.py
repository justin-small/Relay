"""Lighting admin routes (#377). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .backend import BackendError
from .service import KEY, clean_pages, clean_settings, service

router = APIRouter()
VIEW = [Depends(require("lighting.view"))]
CONTROL = [Depends(require("lighting.control"))]
MANAGE = [Depends(require("lighting.manage"))]


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


async def _do(verb: str, cue_id, request, *, page_only: bool) -> dict:
    """Press or release one cue, once. The audit note names the page and the cue ("Lighting: Worship, Verse")."""
    if not isinstance(cue_id, str):
        return {"ok": False, "error": "Choose a cue.", "status": 400}
    found = await service.find(cue_id, page_only=page_only)
    if found is None:
        return {"ok": False, "error": "That cue is not on a lighting page.", "status": 404}
    label, cue_id = found
    try:
        await service.act(verb, cue_id)
    except BackendError as e:
        return {"ok": False, "error": str(e), "status": 502}
    if request is not None:
        audit.note(request, f"lighting.{verb}", f"Lighting: {label}", target=cue_id)
    return {"ok": True}


async def fire(cue_id: str, request=None) -> dict:
    """The hook other modules reach through `app.modules.fire_cue`: only a cue on a page, never the controller's
    whole list."""
    r = await _do("press", cue_id, request, page_only=True)
    r.pop("status", None)
    return r


def _reply(r: dict):
    if r["ok"]:
        return {"ok": True}
    return _bad(r["error"], r["status"])


@router.get("/api/admin/lighting", dependencies=VIEW)
async def admin_state():
    return await service.snapshot()


@router.put("/api/admin/lighting/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    cur = service.settings()
    new = clean_settings({**cur, **{k: payload[k] for k in ("backend", "host", "port") if k in payload},
                          "password": payload["password"] if isinstance(payload.get("password"), str)
                          and payload["password"] else cur["password"]})
    if isinstance(payload.get("host"), str) and payload["host"].strip() and not new["host"]:
        return _bad("That is not an address: use the lighting computer's IP address, like 192.168.1.50.")
    config.save({KEY: new})
    await service.restart()
    audit.note(request, "lighting.settings", "Changed the lighting program", target=new["backend"] or "(none)")
    return await service.snapshot()


@router.put("/api/admin/lighting/pages", dependencies=MANAGE)
async def admin_pages(request: Request, payload: dict):
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return _bad("Send the list of pages.")
    cur = service.settings()
    config.save({KEY: {**cur, "pages": clean_pages(pages)}})
    audit.note(request, "lighting.pages", "Changed the lighting cue pages")
    return await service.snapshot()


@router.post("/api/admin/lighting/test", dependencies=MANAGE)
async def admin_test():
    if service.backend is None:
        return {"ok": False, "detail": "Choose the lighting program first."}
    try:
        return await service.backend.test()
    except Exception:
        return {"ok": False, "detail": "The lighting program did not answer."}


@router.post("/api/admin/lighting/press", dependencies=CONTROL)
async def admin_press(request: Request, payload: dict):
    return _reply(await _do("press", payload.get("cue"), request, page_only=False))


@router.post("/api/admin/lighting/release", dependencies=CONTROL)
async def admin_release(request: Request, payload: dict):
    return _reply(await _do("release", payload.get("cue"), request, page_only=False))
