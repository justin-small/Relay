"""YouTube admin routes (#252). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config, oauth
from .. import require
from .service import ACCOUNT, KEY, clean_settings, service, settings

router = APIRouter()
VIEW = require("youtube.view")
MANAGE = require("youtube.manage")

oauth.add_routes(router, "/api/admin/youtube/account", ACCOUNT, view=VIEW, manage=MANAGE)


@router.get("/api/admin/youtube", dependencies=[Depends(VIEW)])
async def admin_state() -> dict:
    return service.snapshot()


@router.put("/api/admin/youtube/settings", dependencies=[Depends(MANAGE)])
async def admin_settings(request: Request, payload: dict):
    """How often to check, and the church's own Google app (bring your own). A blank secret keeps the saved one;
    `clear_client` removes both. Changing the app does not change an account already connected."""
    cur = settings()
    new = dict(cur)
    for k in ("live_s", "idle_s"):
        if k in payload:
            new[k] = payload[k]
    if payload.get("clear_client") is True:
        new["client_id"], new["client_secret"] = "", ""
    else:
        if isinstance(payload.get("client_id"), str):
            new["client_id"] = payload["client_id"]
        if isinstance(payload.get("client_secret"), str) and payload["client_secret"].strip():
            new["client_secret"] = payload["client_secret"]
    new = clean_settings(new)
    if new["client_id"] and not new["client_id"].endswith(".apps.googleusercontent.com"):
        return JSONResponse({"error": "That is not a Google client ID: it ends in .apps.googleusercontent.com."},
                            status_code=400)
    config.save({KEY: {k: new[k] for k in ("client_id", "client_secret", "live_s", "idle_s")}})
    if (new["client_id"], new["client_secret"]) != (cur["client_id"], cur["client_secret"]):
        audit.note(request, "youtube.client", "Changed the Google app YouTube signs in with",
                   detail={"own_app": bool(new["client_id"])})
    service.poke()
    return service.snapshot()


@router.post("/api/admin/youtube/check", dependencies=[Depends(MANAGE)])
async def admin_check() -> dict:
    """Check now (costs quota, so only for the people who set it up)."""
    await service.check()
    return service.snapshot()
