"""The Intercom live page (#241): one snapshot of who is on and who is talking, and the operator's overrides.

`snapshot()` is the whole state the page draws. The page keeps one open stream (`/api/admin/intercom/live/stream`)
and each press, release, join, leave, mute or label change signals `notify()`, which wakes every open stream; a stream
also refreshes itself every REFRESH_S so a phone's packet loss and jitter move (both come from the watchdog, #276). Nothing here is stored: mutes live in
the arbiter and clear on restart. Each operator action writes one `intercom_events` row with the operator as actor.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ... import db
from . import store
from .arbiter import arbiter

log = logging.getLogger("relay.intercom")

REFRESH_S = 2.0
COALESCE_S = 0.1

label = ""                                   # the production label, kept for the top bar pill

_signal: set[asyncio.Event] = set()


def notify() -> None:
    """Something the page shows changed: wake every open stream."""
    for ev in list(_signal):
        ev.set()


def subscribe() -> asyncio.Event:
    ev = asyncio.Event()
    _signal.add(ev)
    return ev


def unsubscribe(ev: asyncio.Event) -> None:
    _signal.discard(ev)


def _level(t: dict, value: Any, warn_key: str, error_key: str) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return "bad" if value > t[error_key] else "warn" if value > t[warn_key] else "ok"


def _read() -> dict:
    """The database half of a snapshot (runs in a worker thread)."""
    store.ready()
    return {
        "channels": store.channels(),
        "members": db.query("select user_id, channel_id, can_talk, can_listen, priority from intercom_channel_members"),
        "choices": db.query("select user_id, channel_id, listen_selected from intercom_user_choices"),
        "users": {r["id"]: r for r in db.query("select id, name, role from users")},
        "devices": {r["id"]: r for r in db.query("select id, name from intercom_devices")},
        "label": store.production(),
    }


async def snapshot() -> dict:
    from .presence import presence
    from .watchdog import thresholds, watchdog

    global label
    t = thresholds()
    data = await store.run(_read)
    label = data["label"]
    conns = list(presence.conns.values())
    names = {c["id"]: c["name"] for c in data["channels"]}
    listens = {(r["user_id"], r["channel_id"]) for r in data["choices"] if r["listen_selected"]}
    hears = {(m["user_id"], m["channel_id"]) for m in data["members"] if m["can_listen"]}
    priority: dict[int, int] = {}
    for m in data["members"]:
        priority[m["user_id"]] = min(priority.get(m["user_id"], 99), m["priority"])
    channels = []
    for c in data["channels"]:
        cid = c["id"]
        on = {x.user_id for x in conns if (x.user_id, cid) in hears and (x.user_id, cid) in listens}
        channels.append({
            "id": cid, "name": c["name"], "colour": c["colour"], "mode": c["mode"],
            "talking": [l.name for l in arbiter.talkers(cid)], "listening": len(on),
            "waiting": len(arbiter.queues.get(cid, [])), "muted": cid in arbiter.muted_channels})
    connected = []
    for x in conns:
        user = data["users"].get(x.user_id) or {}
        dev = data["devices"].get(x.device_id) or {}
        loss_pct, jitter, _ = watchdog.phone_levels.get(x.device_id, (None, None, ""))
        connected.append({
            "user_id": x.user_id, "device_id": x.device_id, "user": x.name, "role": user.get("role", ""),
            "priority": priority.get(x.user_id), "phone": dev.get("name", ""), "where": "Local",
            "talking": [names[ch] for ch, ls in arbiter.leases.items()
                        if ch in names and any(l.device_id == x.device_id for l in ls.values())],
            "listening": [names[ch] for (u, ch) in sorted(listens) if u == x.user_id and ch in names
                          and (u, ch) in hears],
            "loss_pct": loss_pct, "loss_level": _level(t, loss_pct, "loss_warn_pct", "loss_error_pct"),
            "jitter_ms": jitter, "jitter_level": _level(t, jitter, "jitter_warn_ms", "jitter_error_ms"),
            "muted": arbiter.muted_all or x.user_id in arbiter.muted_users,
            "user_muted": x.user_id in arbiter.muted_users})
    connected.sort(key=lambda r: (r["user"].lower(), r["device_id"]))
    return {"label": label, "connected_count": len(conns), "muted_all": arbiter.muted_all,
            "channels": channels, "connected": connected,
            "note": "Mutes are live state: they clear when Relay restarts."}


def pills() -> list[dict]:
    """The top bar pill every admin page shows while the module is on."""
    from .presence import presence

    n = presence.count()
    text = "INTERCOM LIVE" + (" · " + label if label else "")
    return [{"id": "intercom", "text": text, "count": n, "href": "#intercom"}]


# ------------------------------------------------------------------ the operator's overrides, each one logged

def _log(kind: str, actor_id: int | None, *, user_id: int | None = None, channel_id: int | None = None,
         detail: dict | None = None) -> None:
    with db.transaction() as tx:
        store.log_event(tx, kind, user_id=user_id, channel_id=channel_id, actor_id=actor_id, detail=detail)


async def _done(kind: str, actor_id: int | None, **kw: Any) -> None:
    try:
        await store.run(lambda: _log(kind, actor_id, **kw))
    finally:
        notify()


async def mute_all(on: bool, actor_id: int | None) -> None:
    await arbiter.mute_all(on)
    await _done("mute_all" if on else "unmute_all", actor_id)


async def release_all(actor_id: int | None) -> None:
    await arbiter.release_all()
    await _done("release_all", actor_id)


async def mute_channel(channel_id: int, on: bool, actor_id: int | None) -> None:
    await arbiter.mute_channel(channel_id, on)
    await _done("mute_channel" if on else "unmute_channel", actor_id, channel_id=channel_id)


async def mute_user(user_id: int, on: bool, actor_id: int | None) -> None:
    await arbiter.mute_user(user_id, on)
    await _done("mute_user" if on else "unmute_user", actor_id, user_id=user_id)
