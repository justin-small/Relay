"""Blackmagic ATEM (#243): follows an ATEM switcher's tally and lets the booth cut, take, fade and run macros.

Speaks the ATEM network protocol itself (UDP, port 9910; see protocol.py), so there is nothing to install. Its tally
goes into core's shared tally state, which the tally screens read. For any other switcher, use Tally Arbiter.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class Atem(Module):
    manifest = Manifest(
        id="atem",
        name="Blackmagic ATEM",
        summary="Shows the switcher's program and preview tally on camera screens, and lets the booth cut, take, "
                "fade to black and run macros.",
        settings_key="atem",
        guide="https://github.com/justin-small/relay#cameras-and-tally",
        version="1.1.0",
        traffic=(Traffic("atem", "ATEM switcher control (UDP)"),),
    )
    defaults = {"host": "", "port": 9910}
    admin_pages = (AdminPage(
        id="atem", title="ATEM", template="atem.html", group="Output",
        icon='<rect x="2" y="4" width="12" height="8" rx="1"/><path d="M5 7h2M9 7h2M5 9.5h6"/>'),)
    admin_scripts = ("atem.js",)
    route_prefixes = ("/api/admin/atem",)
    permissions = (
        Permission("atem.view", "See the ATEM's connection, inputs and tally", "ATEM", "viewer"),
        Permission("atem.control", "Cut, take, fade and run macros on the ATEM", "ATEM", "operator"),
        Permission("atem.manage", "Change the ATEM's address", "ATEM", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def detach(self) -> None:
        from ... import tally

        tally.tally.clear("atem")

    async def start(self) -> None:
        from .service import service

        service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def health(self) -> Health:
        from .service import service

        s = service.snapshot()
        if not s["configured"]:
            return Health("warn", "Not set up: enter the switcher's address")
        if s["connected"]:
            return Health("ok", "Connected" + (f" to {s['product']}" if s["product"] else ""))
        return Health("error" if s["error"] else "warn", s["error"] or "Connecting")

    def network_changed(self, resolved) -> None:
        # Its port went away, came back or was re-chosen: open the session again through where it goes now.
        from .service import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="atem-network")

    async def test(self) -> dict:
        from .service import service

        s = service.snapshot()
        if not s["configured"]:
            return {"ok": False, "detail": "Enter the switcher's address first."}
        if s["connected"]:
            return {"ok": True, "detail": "Connected" + (f" to {s['product']}." if s["product"] else ".")}
        return {"ok": False, "detail": s["error"] or "Not connected yet."}


MODULE = Atem()
