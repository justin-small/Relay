"""One LiveKit room per channel, the tokens that let a phone into it, and the live changes to what it may do (#239).

Relay alone decides who hears what. A phone gets a short-lived token for exactly one room (`ch-<channel id>`) it is
a member of, never able to publish at join; the right to publish is switched on and off by Relay in the running
room (a talk lease, #274) and a removed member is taken out of the room at once. The API secret stays on this
computer: phones only ever hold tokens. Tokens are signed by hand (HS256), and the room service is called over
LiveKit's own JSON API on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.request

from . import devices, livekit, store

log = logging.getLogger("relay.intercom")

TOKEN_TTL_S = 300
ADMIN_TTL_S = 60
ROOM_EMPTY_S = 3600
CALL_TIMEOUT_S = 3.0


class RoomError(Exception):
    """A LiveKit call failed. The text is fixed; nothing from LiveKit is passed on."""


def room_name(channel_id: int) -> str:
    return f"ch-{int(channel_id)}"


def identity(user_id: int, device_id: int) -> str:
    return f"u{int(user_id)}-d{int(device_id)}"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(claims: dict, secret: str) -> str:
    head = _b64(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(mac)}"


def mint_token(key: str, secret: str, *, channel_id: int, user_id: int, device_id: int, name: str,
               can_subscribe: bool, now: float | None = None) -> str:
    """A token for one room. Publishing is off at join; only Relay can turn it on, in the running room."""
    t = int(time.time() if now is None else now)
    ident = identity(user_id, device_id)
    return sign({
        "iss": key, "sub": ident, "name": name[:80], "nbf": t - 5, "exp": t + TOKEN_TTL_S,
        "video": {"roomJoin": True, "room": room_name(channel_id), "canSubscribe": bool(can_subscribe),
                  "canPublish": False, "canPublishSources": ["microphone"], "canPublishData": False,
                  "hidden": False},
    }, secret)


def _admin_token(key: str, secret: str, room: str = "") -> str:
    """For LiveKit's room service. Calls on one room need that room named in the token as well."""
    t = int(time.time())
    video = {"roomCreate": True, "roomAdmin": True, "roomList": True}
    if room:
        video["room"] = room
    return sign({"iss": key, "nbf": t - 5, "exp": t + ADMIN_TTL_S, "video": video}, secret)


def _call(method: str, body: dict) -> dict:
    """One call to LiveKit's room service. Blocking: run it in a thread."""
    key, secret = livekit.keys()
    req = urllib.request.Request(
        f"http://127.0.0.1:{livekit.SIGNAL_PORT}/twirp/livekit.RoomService/{method}", json.dumps(body).encode(),
        {"Authorization": "Bearer " + _admin_token(key, secret, str(body.get("room") or body.get("name") or "")),
         "Content-Type": "application/json"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=CALL_TIMEOUT_S) as r:          # noqa: S310  (this computer's own port)
            return json.loads(r.read() or b"{}")
    except OSError as exc:
        if getattr(exc, "code", None) == 404:           # no such room or participant: nothing to do
            return {}
        raise RoomError("LiveKit did not answer.") from None
    except ValueError:
        raise RoomError("LiveKit answered with something unreadable.") from None


async def call(method: str, body: dict) -> dict:
    return await asyncio.to_thread(_call, method, body)


async def ensure_room(channel_id: int) -> None:
    """Create the channel's room (auto-create is off). Asking for one that exists changes nothing."""
    await call("CreateRoom", {"name": room_name(channel_id), "emptyTimeout": ROOM_EMPTY_S})


async def ensure_all() -> None:
    for ch in await store.run(store.channels):
        try:
            await ensure_room(ch["id"])
        except RoomError:
            return          # LiveKit is not up yet: rooms are made again when a token is asked for


def _permission(can_subscribe: bool, can_publish: bool) -> dict:
    return {"canSubscribe": bool(can_subscribe), "canPublish": bool(can_publish), "canPublishData": False,
            "canPublishSources": ["MICROPHONE"]}


async def set_publish(channel_id: int, user_id: int, device_id: int, allowed: bool) -> None:
    """Switch a phone's right to publish in a channel's room, live. This is what a talk lease does."""
    member = await store.run(devices.membership, user_id, channel_id)
    if member is None or (allowed and not member["can_talk"]):
        allowed = False
    await call("UpdateParticipant", {
        "room": room_name(channel_id), "identity": identity(user_id, device_id),
        "permission": _permission(bool(member and member["can_listen"]), allowed)})


async def remove_device(device_id: int, user_id: int) -> None:
    """Take one phone out of every channel's room."""
    rooms = [room_name(c["id"]) for c in await store.run(store.channels)]
    ident = identity(user_id, device_id)
    await asyncio.gather(*(call("RemoveParticipant", {"room": r, "identity": ident}) for r in rooms),
                         return_exceptions=True)


async def remove_user(user_id: int) -> None:
    """Take every phone of a user out of every room (sign-out, a disabled or deleted account)."""
    rooms = [room_name(c["id"]) for c in await store.run(store.channels)]
    await asyncio.gather(*(_sweep(r, user_id, None) for r in rooms), return_exceptions=True)


async def _sweep(room: str, user_id: int, member: dict | None) -> None:
    """Make the user's participants in `room` match `member`: gone with no rights, no subscribing if listening was
    taken away, otherwise untouched."""
    got = await call("ListParticipants", {"room": room})
    prefix = f"u{int(user_id)}-d"
    for p in got.get("participants", []):
        ident = p.get("identity", "")
        if not ident.startswith(prefix):
            continue
        if member is None or not (member["can_listen"] or member["can_talk"]):
            await call("RemoveParticipant", {"room": room, "identity": ident})
        elif not member["can_listen"]:
            await call("UpdateParticipant", {"room": room, "identity": ident,
                                             "permission": _permission(False, False)})


async def member_changed(user_id: int | None, channel_id: int | None) -> None:
    """A user's rights on a channel changed (or the channel went): make the live rooms agree with the database."""
    try:
        if channel_id is None:
            return
        if user_id is None:
            await call("DeleteRoom", {"room": room_name(channel_id)})
            return
        member = await store.run(devices.membership, user_id, channel_id)
        await _sweep(room_name(channel_id), user_id, member)
    except RoomError:
        log.warning("intercom: could not update LiveKit after a membership change")


class Refusal(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.message = text


async def issue(user_id: int, device_id: int, name: str, channel_id) -> dict:
    """A token to join one channel's room, only for a channel the user belongs to."""
    if isinstance(channel_id, bool) or not isinstance(channel_id, int):
        raise Refusal("Pick a channel.")
    member = await store.run(devices.membership, user_id, channel_id)
    if member is None or not (member["can_listen"] or member["can_talk"]):
        raise Refusal("You are not on that channel.")
    try:
        await ensure_room(channel_id)
    except RoomError:
        raise Refusal("The intercom server is not running.") from None
    key, secret = livekit.keys()
    token = mint_token(key, secret, channel_id=channel_id, user_id=user_id, device_id=device_id, name=name,
                       can_subscribe=bool(member["can_listen"]))
    return {"channel": channel_id, "room": room_name(channel_id), "token": token, "expires_in": TOKEN_TTL_S}
