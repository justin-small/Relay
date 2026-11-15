"""The `/intercom/rtc` WebSocket proxy (#238): phones reach LiveKit through Relay's HTTPS origin, never directly.

livekit-server listens on 127.0.0.1 only. A phone's LiveKit client opens `wss://<relay>/intercom/rtc?access_token=...`
(the token Relay minted for one `ch-<id>` room, #239) and this relays frames to LiveKit's own `/rtc` signalling
socket. The token is checked here first (signature, expiry, a join for one of our rooms, a phone that is connected
to Relay right now), so nothing else on the LAN reaches LiveKit's signalling. Media does not pass through here: it
goes over LiveKit's 7881/tcp and 7882/udp.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import time
from urllib.parse import parse_qs

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from . import livekit

log = logging.getLogger("relay.intercom")

PATH = "/intercom/rtc"
LEEWAY_S = 30
MAX_QUERY = 4096
MAX_FRAME = 1024 * 1024
UPSTREAM_WAIT_S = 5
CODE_UPSTREAM = 1013         # LiveKit is not up: try again
ROOM = re.compile(r"^ch-\d{1,9}$")
IDENTITY = re.compile(r"^u(\d{1,9})-d(\d{1,9})$")

# device id -> its open proxy sockets, so signing a phone out cuts its signalling at once.
open_by_device: dict[int, set[WebSocket]] = {}


def _b64url(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def check_token(token: str, key: str, secret: str, now: float | None = None) -> tuple[int, int] | None:
    """(user id, device id) when `token` is a live join token we signed for one `ch-<id>` room, else None."""
    try:
        head_b64, payload_b64, sig_b64 = token.split(".")
        head = json.loads(_b64url(head_b64))
        payload = json.loads(_b64url(payload_b64))
        sig = _b64url(sig_b64)
    except (ValueError, binascii.Error):
        return None
    if not isinstance(head, dict) or not isinstance(payload, dict) or head.get("alg") != "HS256":
        return None
    want = hmac.new(secret.encode(), f"{head_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, want) or payload.get("iss") != key:
        return None
    t = time.time() if now is None else now
    exp, nbf = payload.get("exp"), payload.get("nbf")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or t > exp + LEEWAY_S:
        return None            # a token with no expiry is never ours
    if isinstance(nbf, (int, float)) and not isinstance(nbf, bool) and t < nbf - LEEWAY_S:
        return None
    video = payload.get("video")
    room = video.get("room") if isinstance(video, dict) else None
    if not (isinstance(video, dict) and video.get("roomJoin") is True and isinstance(room, str) and ROOM.match(room)):
        return None            # an admin or room-service token is never accepted from a phone
    sub = payload.get("sub")
    m = IDENTITY.match(sub) if isinstance(sub, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def upstream_url(query: str) -> str:
    """LiveKit's own signalling URL, with the phone's query string passed through unchanged."""
    return f"ws://127.0.0.1:{livekit.SIGNAL_PORT}/rtc" + (f"?{query}" if query else "")


def token_from(ws: WebSocket) -> str:
    got = parse_qs(ws.scope.get("query_string", b"")[:MAX_QUERY].decode("latin-1")).get("access_token", [""])
    if got[0]:
        return got[0]
    auth = ws.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


async def serve(ws: WebSocket) -> None:
    from .presence import on_operator_port, presence

    ip = ws.client.host if ws.client else ""
    if not on_operator_port(ws) or presence._too_many_bad(ip, False):
        await ws.close(code=1008)       # before accept: the handshake is refused (403)
        return
    key, secret = livekit.keys()
    who = check_token(token_from(ws), key, secret)
    if who is None or who[1] not in presence.conns or presence.conns[who[1]].user_id != who[0]:
        presence._too_many_bad(ip, True)
        await ws.close(code=1008)
        return
    device_id = who[1]
    query = ws.scope.get("query_string", b"")[:MAX_QUERY].decode("latin-1")
    try:
        upstream = await asyncio.wait_for(
            websockets.connect(upstream_url(query), max_size=MAX_FRAME, open_timeout=UPSTREAM_WAIT_S), UPSTREAM_WAIT_S + 1)
    except Exception:
        await ws.close(code=CODE_UPSTREAM)
        return
    await ws.accept()
    open_by_device.setdefault(device_id, set()).add(ws)
    try:
        await _pump(ws, upstream)
    finally:
        group = open_by_device.get(device_id)
        if group is not None:
            group.discard(ws)
            if not group:
                open_by_device.pop(device_id, None)
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:        # already closed
            pass


async def _pump(ws: WebSocket, upstream) -> None:
    async def up() -> None:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            elif msg.get("text") is not None:
                await upstream.send(msg["text"])

    async def down() -> None:
        async for frame in upstream:
            if isinstance(frame, bytes):
                await ws.send_bytes(frame)
            else:
                await ws.send_text(frame)

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, WebSocketDisconnect, websockets.ConnectionClosed, RuntimeError):
                pass
            except Exception:
                log.debug("intercom: rtc proxy ended", exc_info=True)


async def drop_device(device_id: int) -> None:
    for ws in list(open_by_device.get(device_id, ())):
        try:
            await ws.close(code=4403, reason="signed_out")
        except Exception:
            pass


async def drop_all() -> None:
    for device_id in list(open_by_device):
        await drop_device(device_id)
