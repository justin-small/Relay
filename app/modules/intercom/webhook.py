"""LiveKit's signed webhook (#238): who joined and left which room, written to the intercom's connection log.

livekit-server POSTs each event to Relay with an `Authorization` header holding a JWT signed (HS256) with the API
secret, whose `sha256` claim is the base64 SHA-256 of the body. Nothing is believed until both check out, so a
phone on the LAN that finds this URL cannot write to the log. Only ids are kept: never a name, a token or audio.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any

from . import livekit, store

log = logging.getLogger("relay.intercom")

PATH = "/intercom/livekit-webhook"
MAX_BODY = 64 * 1024
LEEWAY_S = 60
# LiveKit's event name -> the kind written to intercom_events.
KINDS = {
    "room_started": "room_started",
    "room_finished": "room_finished",
    "participant_joined": "participant_joined",
    "participant_left": "participant_left",
    "track_published": "track_published",
    "track_unpublished": "track_unpublished",
}
ROOM = re.compile(r"^ch-(\d{1,9})$")
IDENTITY = re.compile(r"^u(\d{1,9})-d(\d{1,9})$")

seen = {"count": 0, "last_at": 0.0}


def _b64url(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify(authorization: str, body: bytes, key: str, secret: str, now: float | None = None) -> bool:
    """True when `authorization` is a JWT signed with `secret` for `key` that vouches for exactly this body."""
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        head_b64, payload_b64, sig_b64 = token.split(".")
        head = json.loads(_b64url(head_b64))
        payload = json.loads(_b64url(payload_b64))
        sig = _b64url(sig_b64)
    except (ValueError, binascii.Error):
        return False
    if not isinstance(head, dict) or not isinstance(payload, dict) or head.get("alg") != "HS256":
        return False
    want = hmac.new(secret.encode(), f"{head_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, want):
        return False
    if payload.get("iss") != key:
        return False
    t = time.time() if now is None else now
    exp, nbf = payload.get("exp"), payload.get("nbf")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and t > exp + LEEWAY_S:
        return False
    if isinstance(nbf, (int, float)) and not isinstance(nbf, bool) and t < nbf - LEEWAY_S:
        return False
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    claimed = payload.get("sha256")
    return isinstance(claimed, str) and hmac.compare_digest(claimed.rstrip("="), digest.rstrip("="))


def parse(body: bytes) -> dict | None:
    """The event worth logging as {kind, channel, user, device, detail}, or None for one to ignore."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = KINDS.get(data.get("event"))
    room = data.get("room")
    name = room.get("name") if isinstance(room, dict) else None
    m = ROOM.match(name) if isinstance(name, str) else None
    if kind is None or m is None:
        return None            # not one of ours: the rooms are all `ch-<channel id>`
    user = device = None
    part = data.get("participant")
    ident = part.get("identity") if isinstance(part, dict) else None
    im = IDENTITY.match(ident) if isinstance(ident, str) else None
    if im:
        user, device = int(im.group(1)), int(im.group(2))
    detail: dict[str, Any] = {"room": name}
    track = data.get("track")
    if isinstance(track, dict) and track.get("source") in ("MICROPHONE", "microphone", 2):
        detail["source"] = "microphone"
    return {"kind": kind, "channel": int(m.group(1)), "user": user, "device": device, "detail": detail}


def record(event: dict) -> None:
    """Write one parsed event. Ids that no longer exist (a deleted channel, user or phone) are left out, not refused."""
    from ... import db

    store.ready()
    with db.transaction() as tx:
        ch = event["channel"] if tx.one("select 1 from intercom_channels where id = ?", (event["channel"],)) else None
        user = event["user"] if event["user"] is not None and tx.one(
            "select 1 from users where id = ?", (event["user"],)) else None
        dev = event["device"] if event["device"] is not None and tx.one(
            "select 1 from intercom_devices where id = ?", (event["device"],)) else None
        store.log_event(tx, event["kind"], user_id=user, device_id=dev, channel_id=ch, detail=event["detail"])
    seen["count"] += 1
    seen["last_at"] = time.time()


def key_secret() -> tuple[str, str]:
    return livekit.keys()
