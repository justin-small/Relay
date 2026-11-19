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
        section="Screens",
        guide="https://github.com/justin-small/relay#signage",
    )
    defaults = {"port": DEFAULT_PORT, "override_until": "next_change", "alert_screens": "all",
                "alert_groups": []}
    admin_pages = (AdminPage(
        id="signage", title="Signage", template="admin.html",
        icon='<rect x="1.5" y="2.5" width="13" height="9" rx="1"/><path d="M5.5 14h5M8 11.5V14"/>'),)
    admin_scripts = ("signage.js", "player.js")
    admin_styles = ("signage.css",)
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
        until = raw.get("override_until")
        groups = raw.get("alert_groups")
        groups = sorted({g for g in groups if isinstance(g, int) and not isinstance(g, bool) and g > 0}
                        ) if isinstance(groups, list) else []
        alert = raw.get("alert_screens")
        return {"port": port if 1024 <= port <= 65535 else DEFAULT_PORT,
                "alert_screens": alert if alert in ("all", "groups", "none") else "all",
                "alert_groups": groups[:200],
                "override_until": until if until in ("next_change", "cleared") else "next_change"}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before modules, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from types import SimpleNamespace

        from ... import modules
        from . import live, media, schedule, sources, store, takeover

        sources.register(store.MEDIA_SOURCE)
        # The Schedules page asks every module which of its slots pick a named schedule (#526).
        modules.provide("schedules.used.signage", store.schedule_users)
        # What other modules reach signage through (#523): "signage.content" holds the library, the live state and the
        # schedule they read and change. They ask for it while signage is on and cope with None while it is off.
        modules.provide("signage.content", SimpleNamespace(store=store, media=media, live=live, takeover=takeover,
                                                          schedule=schedule, sources=sources))

    def detach(self) -> None:
        from ... import modules
        from . import sources

        sources.unregister("media")
        modules.withdraw("signage.content")
        modules.withdraw("schedules.used.signage")

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

    # ------------------------------------------------------------------ boards (#156)
    def board_widgets(self) -> list[dict]:
        return [
            {"type": "signage.takeovers", "name": "Signage takeovers", "group": "Signage", "kind": "list", "w": 4,
             "h": 2, "style": {"icon": "play", "label": "Signage"},
             "fields": [{"key": "_", "type": "note", "label": "Which signage screens or groups are showing an alert "
                         "push, a pushed picture or video, or a live view instead of their playlist."}]},
            {"type": "signage.return", "name": "Return signage to playlist", "group": "Signage", "kind": "button",
             "w": 3, "h": 1, "style": {"icon": "arrow", "label": "Return to playlist", "bg": "#1e3a8a", "pressed": "#3b5bdb"},
             "press_permission": "signage.manage",
             "fields": [{"key": "_", "type": "note", "label": "Ends every takeover. Lit while something is taken over."}]},
        ]

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        from ... import db
        from . import store, takeover

        def look() -> tuple[list[dict], dict, dict]:
            return (takeover.rows(), {s["id"]: s["name"] for s in store.screens()},
                    {g["id"]: g["name"] for g in store.groups()})

        try:
            taken, screens, groups = await asyncio.to_thread(look)
        except db.Unavailable:
            taken, screens, groups = [], {}, {}
        out: dict[str, dict] = {}
        for it in items:
            if it.get("type") == "signage.takeovers":
                rows = [{"text": f"{_where(r, screens, groups)}: {r['label']}", "current": True} for r in taken]
                out[it["id"]] = {"rows": rows or [{"text": "Every screen is playing its playlist"}]}
            elif it.get("type") == "signage.return":
                out[it["id"]] = {"lit": bool(taken)}
        return out

    async def board_press(self, item: dict, request) -> dict:
        from ... import audit, db
        from . import takeover

        if item.get("type") != "signage.return":
            return {"ok": False, "error": "That button is off."}
        try:
            n = await asyncio.to_thread(takeover.clear_all)
        except db.Unavailable:
            return {"ok": False, "error": "Signage could not reach its database."}
        audit.note(request, "signage.takeover_clear_all", "Returned every signage screen to its playlist from a board",
                   detail={"cleared": n})
        return {"ok": True}

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


def _where(r: dict, screens: dict, groups: dict) -> str:
    """Which screens a takeover row covers, as words."""
    if r["target"] == "all":
        return "All screens"
    if r["target"] == "group":
        return "Group " + str(groups.get(r["target_id"], "(gone)"))
    return str(screens.get(r["target_id"], "(gone)"))


MODULE = Signage()
