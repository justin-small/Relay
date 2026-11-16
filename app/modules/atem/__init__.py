"""Blackmagic ATEM (#243, #386): follows ATEM switchers' tally and lets the booth cut, take, fade and run macros.

Any number of switchers, each with its own connection and status; the page finds Blackmagic gear on the LAN (mDNS,
and a probe of the subnet when asked) so nobody has to type an address. See service.py and discovery.py.

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
        summary="Shows the switchers' program and preview tally on camera screens, and lets the booth cut, take, "
                "fade to black and run macros. Finds Blackmagic gear on the network.",
        settings_key="atem",
        guide="https://github.com/justin-small/relay#cameras-and-tally",
        version="1.2.0",
        traffic=(Traffic("atem", "ATEM switcher control (UDP)"),),
    )
    defaults = {"devices": []}
    admin_pages = (AdminPage(
        id="atem", title="ATEM", template="atem.html", group="Output",
        icon='<rect x="2" y="4" width="12" height="8" rx="1"/><path d="M5 7h2M9 7h2M5 9.5h6"/>'),)
    admin_scripts = ("atem.js",)
    route_prefixes = ("/api/admin/atem",)
    permissions = (
        Permission("atem.view", "See the ATEM's connection, inputs and tally", "ATEM", "viewer"),
        Permission("atem.control", "Cut, take, fade and run macros on the ATEM", "ATEM", "operator"),
        Permission("atem.manage", "Add, change and remove switchers, and look for them on the network", "ATEM", "admin"),
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
        from .service import service

        service.clear_tally()

    async def start(self) -> None:
        from .service import service

        service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def health(self) -> Health:
        from .service import service

        devs = [d for d in service.snapshot()["devices"] if d["enabled"] and d["host"]]
        if not devs:
            return Health("warn", "Not set up: add a switcher")
        down = [d for d in devs if not d["connected"]]
        if not down:
            if len(devs) == 1:
                return Health("ok", "Connected" + (f" to {devs[0]['product']}" if devs[0]["product"] else ""))
            return Health("ok", f"All {len(devs)} switchers connected")
        text = "; ".join(f"{d['name']}: {d['error'] or 'connecting'}" for d in down)
        return Health("error" if any(d["error"] for d in down) else "warn", text)

    def network_changed(self, resolved) -> None:
        # Its port went away, came back or was re-chosen: open the session again through where it goes now.
        from .service import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="atem-network")

    async def test(self) -> dict:
        from .service import service

        devs = [d for d in service.snapshot()["devices"] if d["enabled"] and d["host"]]
        if not devs:
            return {"ok": False, "detail": "Add a switcher first."}
        parts = [f"{d['name']}: " + (("connected" + (f" to {d['product']}" if d["product"] else ""))
                                     if d["connected"] else (d["error"] or "not connected yet"))
                 for d in devs]
        return {"ok": all(d["connected"] for d in devs), "detail": "; ".join(parts) + "."}


MODULE = Atem()
