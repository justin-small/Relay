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
import re
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


# Pushing to talk has to start audio in well under 100 ms, and a track that is published for the first time takes
# 400 to 700 ms to reach listeners (the #239 spike). So a phone that has Talk selected on a channel it may talk on is
# ARMED: it may publish, it does so at once, and Relay keeps its track MUTED. A talk lease only unmutes it (about
# 70 ms) and its end mutes it again. The cost is that an armed phone could unmute itself, and LiveKit has no event
# for that, so Relay polls armed rooms and mutes any track that is open without a lease. A phone that is not armed
# cannot publish at all.
armed: "set[tuple[int, int]]" = set()          # (channel id, device id) allowed to publish, muted unless leased
talking: "set[tuple[int, int]]" = set()        # the subset that holds a lease now
WATCH_EVERY_S = 0.3
RECONCILE_EVERY_S = 10.0
_IDENT = re.compile(r"^u(\d{1,9})-d(\d{1,9})$")


def _who(ident: str) -> tuple[int, int] | None:
    m = _IDENT.match(ident or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def wants_armed(rights: dict | None) -> bool:
    return bool(rights and rights["can_talk"] and rights.get("talk_selected"))


async def _participants(channel_id: int) -> list[dict]:
    return (await call("ListParticipants", {"room": room_name(channel_id)})).get("participants", [])


async def _mute(channel_id: int, ident: str, sid: str, muted: bool) -> None:
    await call("MutePublishedTrack", {"room": room_name(channel_id), "identity": ident, "trackSid": sid,
                                      "muted": bool(muted)})


def _audio_tracks(p: dict) -> list[dict]:
    return [t for t in p.get("tracks", []) if t.get("type") in ("AUDIO", 0)]


async def apply(user_id: int, channel_id: int) -> None:
    """Make the user's phones in a channel's room match the database: gone with no rights, listening only, or armed."""
    rights = await store.run(devices.rights, user_id, channel_id)
    want_listen = bool(rights and rights["can_listen"])
    want_armed = wants_armed(rights)
    for p in await _participants(channel_id):
        who = _who(p.get("identity", ""))
        if who is None or who[0] != user_id:
            continue
        key = (channel_id, who[1])
        if rights is None or not (rights["can_listen"] or rights["can_talk"]):
            armed.discard(key), talking.discard(key)
            await call("RemoveParticipant", {"room": room_name(channel_id), "identity": p["identity"]})
            continue
        have = p.get("permission") or {}
        if have.get("canSubscribe") != want_listen or have.get("canPublish") != want_armed:
            await call("UpdateParticipant", {"room": room_name(channel_id), "identity": p["identity"],
                                             "permission": _permission(want_listen, want_armed)})
        if want_armed:
            armed.add(key)
        else:
            armed.discard(key), talking.discard(key)


async def set_talking(channel_id: int, user_id: int, device_id: int, on: bool) -> None:
    """A talk lease starts or ends for a phone: unmute or mute its track. If it is not armed yet (the slow path) it
    is armed first and publishes when it sees that."""
    key, ident = (channel_id, device_id), identity(user_id, device_id)
    rights = await store.run(devices.rights, user_id, channel_id)
    if on and not (rights and rights["can_talk"]):
        on = False
    if on:
        talking.add(key)
        if key not in armed:
            armed.add(key)
            await call("UpdateParticipant", {"room": room_name(channel_id), "identity": ident,
                                             "permission": _permission(bool(rights["can_listen"]), True)})
    else:
        talking.discard(key)
    for p in await _participants(channel_id):
        if p.get("identity") == ident:
            await asyncio.gather(*(_mute(channel_id, ident, t["sid"], not on) for t in _audio_tracks(p)
                                   if bool(t.get("muted")) == on))


async def enforce(channel_id: int, only_device: int | None = None) -> None:
    """Mute any open track in the room whose phone holds no lease. Muting a muted track is skipped."""
    mine = {k[1] for k in armed if k[0] == channel_id and k not in talking and (only_device in (None, k[1]))}
    if not mine:
        return
    for p in await _participants(channel_id):
        who = _who(p.get("identity", ""))
        if who is None or who[1] not in mine:
            continue
        for t in _audio_tracks(p):
            if not t.get("muted"):
                await _mute(channel_id, p["identity"], t["sid"], True)
                log.warning("intercom: muted a track that was open without a talk lease")


async def reconcile() -> None:
    """Heal drift (Relay or LiveKit restarted, a missed change): every phone in every room gets what it should."""
    for ch in await store.run(store.channels):
        seen: set[int] = set()
        for p in await _participants(ch["id"]):
            who = _who(p.get("identity", ""))
            if who and who[0] not in seen:
                seen.add(who[0])
                await apply(who[0], ch["id"])


async def watch() -> None:
    """The module's background loop: mute open tracks without a lease, and now and then reconcile."""
    last = 0.0
    while True:
        try:
            if time.monotonic() - last >= RECONCILE_EVERY_S:
                last = time.monotonic()
                await reconcile()
            for channel_id in {k[0] for k in armed}:
                await enforce(channel_id)
        except RoomError:
            pass            # LiveKit is down or restarting; the next pass tries again
        except Exception:
            log.exception("intercom: room watch failed")
        await asyncio.sleep(WATCH_EVERY_S)


async def event(ev: dict) -> None:
    """A LiveKit webhook event, already verified and parsed (webhook.parse): keep armed state and tracks in line."""
    try:
        ch, user, dev = ev["channel"], ev["user"], ev["device"]
        if user is None or dev is None:
            return
        if ev["kind"] == "participant_joined":
            await apply(user, ch)
        elif ev["kind"] == "participant_left":
            armed.discard((ch, dev)), talking.discard((ch, dev))
        elif ev["kind"] == "track_published":
            await enforce(ch, dev)
    except RoomError:
        log.warning("intercom: could not act on a LiveKit event")
    except Exception:
        log.exception("intercom: acting on a LiveKit event failed")


async def remove_device(device_id: int, user_id: int) -> None:
    """Take one phone out of every channel's room."""
    chans = [c["id"] for c in await store.run(store.channels)]
    ident = identity(user_id, device_id)
    for c in chans:
        armed.discard((c, device_id)), talking.discard((c, device_id))
    await asyncio.gather(*(call("RemoveParticipant", {"room": room_name(c), "identity": ident}) for c in chans),
                         return_exceptions=True)


async def remove_user(user_id: int) -> None:
    """Take every phone of a user out of every room (sign-out, a disabled or deleted account)."""
    chans = [c["id"] for c in await store.run(store.channels)]
    await asyncio.gather(*(apply(user_id, c) for c in chans), return_exceptions=True)


async def member_changed(user_id: int | None, channel_id: int | None) -> None:
    """A user's rights on a channel changed (or the channel went): make the live rooms agree with the database."""
    try:
        if channel_id is None:
            return
        if user_id is None:
            for k in [k for k in armed if k[0] == channel_id]:
                armed.discard(k), talking.discard(k)
            await call("DeleteRoom", {"room": room_name(channel_id)})
            return
        await apply(user_id, channel_id)
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
