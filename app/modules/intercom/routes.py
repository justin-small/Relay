"""Intercom's admin routes. Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .. import require
from . import store, webhook
from .livekit import supervisor

router = APIRouter()


@router.get("/api/admin/intercom", dependencies=[Depends(require("intercom.view"))])
async def intercom_view():
    """LiveKit's state and log tail. No key, secret or config text: they never leave the server."""
    return supervisor.view()


@router.post("/api/admin/intercom/restart", dependencies=[Depends(require("intercom.manage"))])
async def intercom_restart():
    await supervisor.restart()
    return supervisor.view()


@router.post(webhook.PATH)
async def livekit_webhook(request: Request):
    """livekit-server's signed events. Public on purpose: the signature, not a sign-in, is the credential."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > webhook.MAX_BODY:
        return JSONResponse({"error": "Too large"}, status_code=413)
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > webhook.MAX_BODY:
            return JSONResponse({"error": "Too large"}, status_code=413)
    key, secret = webhook.key_secret()
    if not webhook.verify(request.headers.get("authorization", ""), body, key, secret):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    event = webhook.parse(body)
    if event is not None:
        try:
            await store.run(webhook.record, event)
        except Exception:        # the database being down must not make LiveKit retry or Relay log a body
            webhook.log.warning("intercom: could not log a LiveKit event")
    return Response(status_code=200)
