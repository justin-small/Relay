"""Intercom remote (#277): crew talk from outside the building, over Cloudflare Realtime.

Off by default; it needs `intercom` and `remote_access`. A remote phone connects straight to Cloudflare Realtime, never
to this computer's WebRTC ports; Relay stays in charge of the matrix, bridging each channel between LiveKit and
Cloudflare and deciding every permission through the same talk arbiter as a local phone. The Realtime app secret is
held by relay-cloud, not by this install. Everything here is outbound, so losing the internet, or switching this module
off, never interrupts local audio.
"""
from __future__ import annotations

import logging
from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission

log = logging.getLogger("relay.intercom_remote")


class IntercomRemote(Module):
    manifest = Manifest(
        id="intercom_remote",
        name="Intercom remote",
        version="0.1.0",
        summary="Lets allowed crew use the intercom from outside the building, through Cloudflare Realtime.",
        settings_key="intercom_remote",
        guide="https://github.com/justin-small/relay#intercom-remote",
        uses=("intercom", "remote_access"),
    )
    defaults: dict = {}
    admin_pages = (AdminPage(
        id="intercom-remote", title="Remote crew", template="admin.html", group="Automation",
        icon='<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2.5 4 2.5 6S10 12 8 14M8 2C6 4 5.5 6 5.5 8S6 12 8 14"/>'),)
    admin_scripts = ("remote.js",)
    route_prefixes = ("/api/admin/intercom-remote", "/intercom/remote")
    microphone_pages = ("/intercom/remote/",)      # the remote phone page talks; Chrome refuses the mic without this
    permissions = (
        Permission("intercom_remote.view", "See who may use the intercom remotely and the link to Cloudflare",
                   "Intercom remote", "viewer"),
        Permission("intercom_remote.manage", "Allow or cut off remote intercom users", "Intercom remote", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        return {}

    def configured(self, settings: dict) -> bool:
        return False

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ..intercom import presence as _p  # noqa: F401  (the intercom is named in `uses`)
        from .bridge import bridge
        from .remote import crew

        bridge.on_lost = crew.drop_all

    def detach(self) -> None:
        from .bridge import bridge

        bridge.on_lost = None

    async def start(self) -> None:
        from . import media
        from .bridge import bridge

        bridge.begin(media.load())

    async def stop(self) -> None:
        from .bridge import bridge
        from .remote import crew

        await crew.drop_all("module_off")
        await bridge.end()

    def health(self) -> Health:
        from ..remote_access.service import enrolled
        from .bridge import bridge

        if not enrolled():
            return Health("warn", "Enroll this install on the Remote access page first")
        s = bridge.state
        if s == "connected":
            return Health("ok", "Connected to Cloudflare")
        if s == "missing":
            return Health("error", bridge.detail)
        if s in ("connecting", "reconnecting"):
            return Health("warn", bridge.detail or "Connecting to Cloudflare")
        return Health("idle", "The bridge is not running")

    async def test(self) -> dict:
        from . import cloud

        ok = await cloud.run(cloud.reachable)
        return {"ok": ok, "detail": "Relay's cloud answered." if ok else "Couldn't reach Relay's cloud."}

    def status(self) -> dict:
        return {}

    def pills(self) -> list:
        return []


MODULE = IntercomRemote()
