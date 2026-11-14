"""Digital signage (#204, #205): turn any TV with a browser into a signage screen.

Operators upload images and videos, build playlists, and give each screen an address, /signage/<slug>, on a
plain-HTTP port of its own (8090 by default), so a smart TV needs no login and no certificate. Screens can be put
in groups that share a playlist. Players pick up every change live and keep playing what they have when Relay
restarts. Other modules add kinds of item through `sources` (see sources.py); signage imports none of them.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

from .. import AdminPage, Health, Listener, Manifest, Module
from ...users import Permission

log = logging.getLogger("relay.signage")

DEFAULT_PORT = 8090
PRUNE_EVERY_S = 6 * 3600


class Signage(Module):
    manifest = Manifest(
        id="signage",
        name="Signage",
        version="1.1.0",
        summary="Plays your images and videos on any TV with a web browser, each screen at its own address.",
        settings_key="signage",
        guide="https://github.com/justin-small/relay#signage",
    )
    defaults = {"port": DEFAULT_PORT, "timezone": "", "override_until": "next_change", "alert_screens": "all",
                "alert_groups": []}
    admin_pages = (AdminPage(
        id="signage", title="Signage", template="admin.html", group="Output",
        icon='<rect x="1.5" y="2.5" width="13" height="9" rx="1"/><path d="M5.5 14h5M8 11.5V14"/>'),)
    admin_scripts = ("signage.js", "player.js")
    route_prefixes = ("/signage", "/api/admin/signage")
    listeners = (Listener(name="screens", label="signage screens", port_setting="port", default_port=DEFAULT_PORT,
                          prefixes=("/signage",)),)
    permissions = (
        Permission("signage.view", "See signage screens, playlists and media", "Signage", "viewer"),
        Permission("signage.manage", "Upload media and change signage screens, groups and playlists", "Signage",
                   "admin"),
    )

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def clean(self, raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        try:
            port = int(raw.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        from ... import schedules

        tz = raw.get("timezone")
        until = raw.get("override_until")
        groups = raw.get("alert_groups")
        groups = sorted({g for g in groups if isinstance(g, int) and not isinstance(g, bool) and g > 0}
                        ) if isinstance(groups, list) else []
        alert = raw.get("alert_screens")
        return {"port": port if 1024 <= port <= 65535 else DEFAULT_PORT,
                "alert_screens": alert if alert in ("all", "groups", "none") else "all",
                "alert_groups": groups[:200],
                "timezone": tz if tz in schedules.TZ_LABELS else schedules.default_timezone(os.environ.get("TZ")),
                "override_until": until if until in ("next_change", "cleared") else "next_change"}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before modules, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from . import sources, store

        sources.register(store.MEDIA_SOURCE)

    def detach(self) -> None:
        from . import sources

        sources.unregister("media")

    async def start(self) -> None:
        self._task = asyncio.create_task(self._housekeeping())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _housekeeping(self) -> None:
        """Copy any media the backups folder lacks and prune copies nothing names, now and every few hours."""
        from ... import db
        from . import media, store, takeover

        while True:
            try:
                rows = await asyncio.to_thread(store.media_list)
                await asyncio.to_thread(media.mirror_missing, rows)
                await asyncio.to_thread(media.prune_mirror, {r["sha256"] for r in rows})
                await asyncio.to_thread(store.sweep_overrides)
                await asyncio.to_thread(takeover.sweep)
            except db.Unavailable:
                pass
            except Exception:
                log.exception("signage housekeeping failed")
            await asyncio.sleep(PRUNE_EVERY_S)

    def health(self) -> Health:
        from ... import serving
        from ... import config

        port = config.get()["signage"]["port"]
        if not serving.published():
            return Health("ok", f"Screens on port {port}")
        bound = serving.listener_bound(self.listeners[0].prefixes)
        if bound is None:
            return Health("warn", f"Restart Relay to open port {port} for the screens")
        if bound != port:
            return Health("warn", f"Screens are on port {bound}; restart Relay to move them to {port}")
        return Health("ok", f"Screens on port {bound}")


MODULE = Signage()
