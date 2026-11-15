"""Remote crew's routes (#277).

Two groups. The admin routes (`/api/admin/intercom-remote`) answer only on the operator port. The remote routes are
exactly `REMOTE_ROUTES` and nothing else: the tunnel points at the viewer port, so these two are the whole of what
remote intercom puts on the internet. tests/test_intercom_remote.py fails when that list and the real routes differ.
"""
from __future__ import annotations

import json
import time
from collections import deque

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import JSONResponse

from .. import require
from ... import users
from ..intercom import devices, store as istore
from ..intercom.presence import presence
from . import remote, store
from .bridge import bridge

router = APIRouter()

REMOTE_ROUTES = (("POST", "/intercom/remote/api/sign-in"), ("WEBSOCKET", "/intercom/remote/ws"))
SIGN_INS_PER_MIN = 10          # per address; #278 adds the shared limiter
MAX_BODY = 2048

_tries: dict[str, deque[float]] = {}


def _slow(request: Request) -> bool:
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
    now = time.monotonic()
    q = _tries.setdefault(ip, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= SIGN_INS_PER_MIN:
        return True
    q.append(now)
    if len(_tries) > 1000:
        for k in [k for k, v in _tries.items() if not v]:
            del _tries[k]
    return False


async def _body(request: Request) -> dict:
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            return {}
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/intercom/remote/api/sign-in")
async def remote_sign_in(request: Request):
    """Sign in from outside the building: a Relay account that an admin marked remote allowed gets its own credential."""
    if _slow(request):
        return JSONResponse({"error": "Too many tries. Wait a minute."}, status_code=429)
    data = await _body(request)
    try:
        credential, row, user = await istore.run(lambda: store.sign_in(
            data.get("username"), data.get("password"), name=data.get("name", ""),
            user_agent=request.headers.get("user-agent", "")))
    except devices.Refused as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status)
    return {"credential": credential, "phone": {"id": row["id"], "name": row["name"]},
            "user": {"id": user.id, "name": user.name}, "socket": "/intercom/remote/ws"}


@router.websocket("/intercom/remote/ws")
async def remote_socket(ws: WebSocket):
    await remote.serve(ws)


# ------------------------------------------------------------------ admin
def view() -> dict:
    return {"bridge": {"state": bridge.state, "detail": bridge.detail, "restarts": bridge.restarts,
                       "remote_phones": len(remote.crew.states)}}


@router.get("/api/admin/intercom-remote", dependencies=[Depends(require("intercom_remote.view"))])
async def remote_view():
    return {**view(), "people": await istore.run(store.people), "sessions": await istore.run(store.sessions)}


@router.post("/api/admin/intercom-remote/users/{user_id}/allow")
async def remote_allow(user_id: int, request: Request, user: users.User = Depends(require("intercom_remote.manage"))):
    data = await _body(request)
    on = data.get("allowed") is True
    try:
        await istore.run(store.set_allowed, user_id, on, user.id)
        if not on:
            for device_id in await istore.run(store.revoke_user_phones, user_id, user.id):
                await presence.revoked({"id": device_id, "user_id": user_id})
            await remote.drop_user(user_id, "remote_removed")
    except devices.Refused as exc:
        return JSONResponse({"error": exc.message}, status_code=exc.status)
    return {"ok": True}


@router.post("/api/admin/intercom-remote/users/{user_id}/cut")
async def remote_cut(user_id: int, user: users.User = Depends(require("intercom_remote.manage"))):
    """Sign out one user's remote phones. Their local phones stay."""
    ids = await istore.run(store.revoke_user_phones, user_id, user.id)
    for device_id in ids:
        await presence.revoked({"id": device_id, "user_id": user_id})
    await remote.drop_user(user_id, "cut")
    return {"cut": len(ids)}
