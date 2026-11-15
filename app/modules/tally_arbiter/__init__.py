"""Tally Arbiter (#244): follows a Tally Arbiter server's tally, for every switcher Relay does not speak itself.

Tally Arbiter reads tally from ATEM, OBS, vMix and many others. This module listens to it and publishes into core's
shared tally state, which the tally screens read. For a Blackmagic ATEM, the ATEM module needs no extra server.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission


class TallyArbiter(Module):
    manifest = Manifest(
        id="tally_arbiter",
        name="Tally Arbiter",
        version="1.0.0",
        summary="Shows the tally Tally Arbiter serves (ATEM, OBS, vMix and others) on Relay's camera screens.",
        settings_key="tally_arbiter",
        guide="https://github.com/justin-small/relay#cameras-and-tally",
    )
    defaults = {"host": "", "port": 4455}
    admin_pages = (AdminPage(
        id="tallyarbiter", title="Tally Arbiter", template="tally_arbiter.html", group="Output",
        icon='<circle cx="8" cy="8" r="5"/><circle cx="8" cy="8" r="1.6"/>'),)
    admin_scripts = ("tally_arbiter.js",)
    route_prefixes = ("/api/admin/tally-arbiter",)
    permissions = (
        Permission("tally_arbiter.view", "See Tally Arbiter's connection and cameras", "Tally Arbiter", "viewer"),
        Permission("tally_arbiter.manage", "Change Tally Arbiter's address", "Tally Arbiter", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .listener import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def detach(self) -> None:
        from ... import tally

        tally.tally.clear("tally_arbiter")

    async def start(self) -> None:
        from .listener import service

        service.start()

    async def stop(self) -> None:
        from .listener import service

        await service.stop()

    def health(self) -> Health:
        from .listener import service

        s = service.snapshot()
        if not s["configured"]:
            return Health("warn", "Not set up: enter Tally Arbiter's address")
        if s["connected"]:
            n = len(s["cameras"])
            return Health("ok", f"Connected, {n} camera{'s' if n != 1 else ''}")
        return Health("error" if s["error"] else "warn", s["error"] or "Connecting")

    async def test(self) -> dict:
        from .listener import service

        s = service.snapshot()
        if not s["configured"]:
            return {"ok": False, "detail": "Enter Tally Arbiter's address first."}
        if s["connected"]:
            return {"ok": True, "detail": "Connected."}
        return {"ok": False, "detail": s["error"] or "Not connected yet."}


MODULE = TallyArbiter()
