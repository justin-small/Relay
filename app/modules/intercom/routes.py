"""Intercom's admin routes. Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import JSONResponse, Response

from .. import require
from ... import db, users
from . import devices, rooms, rtcproxy, store, webhook
from .livekit import supervisor
from .presence import on_operator_port, presence

router = APIRouter()


@router.get("/api/admin/intercom", dependencies=[Depends(require("intercom.view"))])
async def intercom_view():
    """LiveKit's state and log tail. No key, secret or config text: they never leave the server."""
    return supervisor.view()


@router.post("/api/admin/intercom/restart", dependencies=[Depends(require("intercom.manage"))])
async def intercom_restart():
    await supervisor.restart()
    return supervisor.view()


# ------------------------------------------------------------------ signed-in phones (#273)

STALE_S = 30 * 86400


def _phone(row: dict) -> dict:
    """A phone as the pages see it: no credential, no hash."""
    seen = row["last_seen_at"] or row["issued_at"]
    status = "On intercom" if presence.online(row["id"]) else (
        "Expired" if seen < db.later(-STALE_S) else "Idle")
    return {"id": row["id"], "user_id": row["user_id"], "user": row["user_name"], "name": row["name"],
            "platform": row["platform"], "signed_in": row["issued_at"], "last_seen": row["last_seen_at"],
            "renews_at": row["rotates_at"], "status": status}


async def _body(request: Request) -> dict:
    raw = await _read_limited(request, 2048)
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _read_limited(request: Request, limit: int) -> bytes:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return b""
    return body


def _refused(exc: devices.Refused) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


async def _signed_out(row: dict) -> None:
    await presence.revoked(row)


@router.post("/intercom/api/phones")
async def phone_sign_in(request: Request, user: users.User = Depends(require("intercom.use"))):
    """"Keep this phone signed in": make this phone's device and hand back its credential, once."""
    if not on_operator_port(request):         # type: ignore[arg-type]
        return JSONResponse({"error": "Not found"}, status_code=404)
    data = await _body(request)
    credential, row = await store.run(lambda: devices.create(
        user.id, name=data.get("name", ""), user_agent=request.headers.get("user-agent", ""),
        platform=data.get("platform", "web")))
    return {"credential": credential, "phone": {"id": row["id"], "name": row["name"], "org_id": row["org_id"]}}


@router.get("/intercom/api/my-phones")
async def my_phones(user: users.User = Depends(require("intercom.use"))):
    return {"phones": [_phone(r) for r in await store.run(devices.listing, user.id)]}


@router.post("/intercom/api/my-phones/{device_id}/rename")
async def my_phone_rename(device_id: int, request: Request, user: users.User = Depends(require("intercom.use"))):
    data = await _body(request)
    try:
        await store.run(lambda: devices.rename(device_id, data.get("name", ""), user_id=user.id))
    except devices.Refused as exc:
        return _refused(exc)
    return {"ok": True}


@router.delete("/intercom/api/my-phones/{device_id}")
async def my_phone_sign_out(device_id: int, user: users.User = Depends(require("intercom.use"))):
    row = await store.run(lambda: devices.revoke(device_id, user_id=user.id, actor_id=user.id))
    if row is None:
        return JSONResponse({"error": "That phone is not signed in any more."}, status_code=404)
    await _signed_out(row)
    return {"ok": True}


@router.get("/api/admin/intercom/phones", dependencies=[Depends(require("intercom.manage"))])
async def phones():
    return {"phones": [_phone(r) for r in await store.run(devices.listing)]}


@router.post("/api/admin/intercom/phones/signout-all")
async def phones_sign_out_all(user: users.User = Depends(require("intercom.manage"))):
    gone = await store.run(lambda: devices.revoke_all(user.id))
    for device_id, user_id in gone:
        await _signed_out({"id": device_id, "user_id": user_id})
    return {"signed_out": len(gone)}


@router.post("/api/admin/intercom/phones/{device_id}/rename")
async def phone_rename(device_id: int, request: Request, _: users.User = Depends(require("intercom.manage"))):
    data = await _body(request)
    try:
        await store.run(lambda: devices.rename(device_id, data.get("name", "")))
    except devices.Refused as exc:
        return _refused(exc)
    return {"ok": True}


@router.delete("/api/admin/intercom/phones/{device_id}")
async def phone_sign_out(device_id: int, user: users.User = Depends(require("intercom.manage"))):
    row = await store.run(lambda: devices.revoke(device_id, actor_id=user.id))
    if row is None:
        return JSONResponse({"error": "That phone is not signed in any more."}, status_code=404)
    await _signed_out(row)
    return {"ok": True}


@router.websocket("/intercom/ws")
async def intercom_socket(ws: WebSocket):
    """The phone's socket. It signs in with its device credential, not a cookie or a password."""
    await presence.serve(ws)


@router.websocket(rtcproxy.PATH)
async def intercom_rtc(ws: WebSocket):
    """LiveKit's signalling for a phone, relayed to 127.0.0.1. The join token Relay minted is the credential."""
    await rtcproxy.serve(ws)


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
        await rooms.event(event)       # keeps armed phones and their tracks in line
    return Response(status_code=200)
