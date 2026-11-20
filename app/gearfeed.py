"""The one seam a module offers its gear, ports and channels through (#545, part of the gear/ports/channels epic #539).

A module that has equipment, connectors or channels offers `modules.provide("gear.<module id>", {...})` in `attach()` (the
registry takes it back when the module is switched off, and with it the module's gear drops out of the live map; what was
saved about that gear stays). Its `list` answers "what do you have right now":

    {"devices":  [{"mac", "ip", "host", "name", "kind", "tags", ...}],     # boxes for the network map, as network_map.py
     "ports":    [{"gear": <gear id>, "items": [{"key", "kind", "direction", "gear_name", ...}]}],
     "channels": [{"gear": <gear id> or None, "within": "<key prefix>", "items": [{"key", "kind", "direction", ...}]}],
     "adds":     "one sentence for the map's Sources card"}

and `list()` returns None while the module cannot see its gear (a Videohub that is not connected does not say how many inputs
it has), so nothing it reported turns missing. The same dict may hold the rename pair core asks for (`can_rename`,
`rename`; app/channels.py) and nothing else a module must register.

The module calls `gearfeed.changed("<module id>")` when what it has changed (a scene list read, a device connected); core
asks `list()` and records the answer as gear, ports and channels. Core never imports a module. Everything here blocks
(database): from async code call it through `asyncio.to_thread`.
"""
from __future__ import annotations

import logging
import sqlite3

from . import channels, db, modules, ports

log = logging.getLogger("relay.gearfeed")

PREFIX = "gear."


def offer(provider: str) -> dict | None:
    """What `provider` offers through `gear.<id>`, or None while it is off or offers nothing."""
    api = modules.using(PREFIX + provider)
    return api if isinstance(api, dict) else None


def ask(provider: str) -> dict | None:
    """What `provider` has now (its `list()`), or None while it is off or offers no `list`. Its own error goes to the caller."""
    api = offer(provider)
    fn = api.get("list") if api else None
    got = fn() if callable(fn) else None
    return got if isinstance(got, dict) else None


def collect(provider: str) -> dict | None:
    """`ask`, but a module's own failure is logged and answered with None: one module must not take the others down."""
    try:
        return ask(provider)
    except Exception:  # noqa: BLE001
        log.warning("gear: %s could not say what it has", provider, exc_info=True)
        return None


def devices(provider: str) -> list[dict]:
    """The boxes `provider` adds to the network map while it is on. A failure in the module raises (the map shows a fixed
    sentence and keeps the last good read)."""
    return [d for d in (ask(provider) or {}).get("devices") or [] if isinstance(d, dict)]


def changed(provider: str) -> dict[str, str] | None:
    """What `provider` has changed: ask it and record the answer. The database being down is logged and skipped, so a
    module's own work never stops for it. Returns {channel key: ref} for the channels recorded (what `channels.report` did),
    or None when nothing was recorded."""
    got = collect(provider)
    if got is None:
        return None
    out: dict[str, str] = {}
    try:
        for group in got.get("ports") or []:
            if isinstance(group, dict) and isinstance(group.get("gear"), int):
                ports.sync(group["gear"], list(group.get("items") or []))
        for group in got.get("channels") or []:
            if isinstance(group, dict):
                out.update(channels.sync(provider, list(group.get("items") or []), group.get("gear"),
                                         str(group.get("within") or "")))
    except (db.Unavailable, sqlite3.Error, ports.Refused):
        log.info("gear: the database is not ready, so %s's gear was not recorded", provider)
        return None
    return out
