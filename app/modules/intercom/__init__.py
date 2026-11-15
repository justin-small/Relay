"""Intercom (#237): phone intercom for the crew, over a media server Relay runs on this computer.

This slice holds the module's tables (#272) and the supervised local livekit-server (#238). Channels, talk
leases and the crew page come in the later issues of the epic. Everything stays on the LAN: no cloud, no Docker.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from .. import AdminPage, CrewTab, Health, Manifest, Module
from ...users import Permission

log = logging.getLogger("relay.intercom")

PRUNE_EVERY_S = 24 * 3600


def _text(v: Any, limit: int = 500) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


class Intercom(Module):
    manifest = Manifest(
        id="intercom",
        name="Intercom",
        version="0.1.0",
        summary="Crew intercom on phones over the church network, using a media server Relay runs itself.",
        settings_key="intercom",
        guide="https://github.com/justin-small/relay#intercom",
    )
    defaults = {"binary_path": "", "node_ip": "", "log_days": 30}
    admin_pages = (AdminPage(
        id="intercom", title="Intercom", template="admin.html", group="Automation",
        icon='<path d="M2.5 9.5v-2a5.5 5.5 0 0 1 11 0v2"/><rect x="1.5" y="9" width="3" height="4.5" rx="1"/>'
             '<rect x="11.5" y="9" width="3" height="4.5" rx="1"/>'),
        AdminPage(id="intercom-phones", title="Signed-in phones", template="phones.html", group="Automation",
                  icon='<rect x="4.5" y="1.5" width="7" height="13" rx="1.5"/><path d="M7 12.5h2"/>',
                  permission="intercom.manage"))
    admin_scripts = ("intercom.js", "live.js")
    crew_tabs = (CrewTab("intercom", "Intercom", "crew.html", "intercom.use"),)
    crew_scripts = ("livekit-client.umd.js", "crew.js")
    microphone_pages = ("/crew",)
    route_prefixes = ("/api/admin/intercom", "/intercom")
    permissions = (
        Permission("intercom.use", "Use the crew intercom on a phone", "Intercom", "member"),
        Permission("intercom.view", "See the intercom media server and its log", "Intercom", "viewer"),
        Permission("intercom.live", "Mute, release talk and set the production label on the intercom live page",
                   "Intercom", "operator"),
        Permission("intercom.manage", "Restart the intercom media server and change its settings", "Intercom",
                   "admin"),
    )

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._watch: asyncio.Task | None = None
        self._leases: asyncio.Task | None = None

    def attach(self) -> None:
        from ... import users
        from . import store
        from .presence import presence

        if presence.user_signed_out not in users.signed_out_hooks:
            users.signed_out_hooks.append(presence.user_signed_out)
        if presence.member_changed not in store.member_hooks:
            store.member_hooks.append(presence.member_changed)
        if presence.config_changed not in store.config_hooks:
            store.config_hooks.append(presence.config_changed)

    def detach(self) -> None:
        from ... import users
        from . import store
        from .presence import presence

        for lst, fn in ((users.signed_out_hooks, presence.user_signed_out), (store.member_hooks,
                                                                           presence.member_changed),
                        (store.config_hooks, presence.config_changed)):
            if fn in lst:
                lst.remove(fn)

    def clean(self, raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        days = raw.get("log_days")
        days = days if isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 365 else 30
        return {"binary_path": _text(raw.get("binary_path")), "node_ip": _text(raw.get("node_ip"), 64),
                "log_days": days}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    async def start(self) -> None:
        from ... import config
        from .livekit import supervisor

        from .presence import presence

        from . import rooms

        from .arbiter import arbiter

        from . import live, store

        presence.loop = asyncio.get_running_loop()
        presence.bind()
        try:
            live.label = await store.run(store.production)
        except Exception:
            log.warning("intercom: could not read the production label")
        self._leases = asyncio.create_task(arbiter.loop(), name="intercom-talk-leases")
        supervisor.begin(config.get()["intercom"])
        self._task = asyncio.create_task(self._housekeeping())
        self._watch = asyncio.create_task(rooms.watch(), name="intercom-room-watch")

    async def stop(self) -> None:
        from .livekit import supervisor

        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        from . import rooms
        from .presence import presence

        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watch
            self._watch = None
        from .arbiter import arbiter

        if self._leases is not None:
            self._leases.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._leases
            self._leases = None
        arbiter.reset()
        from . import rtcproxy

        await rtcproxy.drop_all()
        rooms.armed.clear()
        rooms.talking.clear()
        for device_id in list(presence.conns):
            await presence.drop(device_id, 1001, "server_stopping")
        presence.loop = None
        await supervisor.end()

    async def _housekeeping(self) -> None:
        """Prune the connection log of rows past its retention, now and daily."""
        from ... import config, db
        from . import store

        while True:
            try:
                await store.run(store.prune_events, config.get()["intercom"]["log_days"])
            except db.Unavailable:
                pass
            except Exception:
                log.exception("intercom housekeeping failed")
            await asyncio.sleep(PRUNE_EVERY_S)

    def health(self) -> Health:
        from .livekit import supervisor

        s = supervisor
        if s.state == "running":
            return Health("ok", "LiveKit is running")
        if s.state in ("downloading", "starting", "restarting"):
            return Health("warn", s.detail or "LiveKit is starting")
        if s.state in ("missing", "error"):
            return Health("error", s.detail)
        return Health("idle", "LiveKit is not running")

    async def test(self) -> dict:
        from ... import config
        from .livekit import asset, find_binary

        if find_binary(config.get()["intercom"]["binary_path"]):
            return {"ok": True, "detail": "livekit-server is in place."}
        if asset() is None:
            return {"ok": False, "detail": "Install livekit-server and enter its path under Settings."}
        return {"ok": True, "detail": "livekit-server will be downloaded the first time the module starts."}

    def status(self) -> dict:
        return {}

    def pills(self) -> list:
        from . import live

        return live.pills()


MODULE = Intercom()
