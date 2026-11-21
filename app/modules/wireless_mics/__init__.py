"""Wireless mics (#153), slice 1: Shure receivers and manual mics and packs, tied to Planning Center positions.

UNPROVEN ON HARDWARE: the Shure client was written from Shure's command strings and tested against a fake receiver; no
Shure receiver has been available. Sennheiser EW-DX, Alerts on low battery and cards on /screen come in later slices.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class WirelessMics(Module):
    manifest = Manifest(
        id="wireless_mics",
        name="Wireless mics",
        version="0.1.0",
        summary="Shows battery, signal, audio and mute for Shure wireless receivers, and lists manual mics and "
                "in-ear packs, each tied to a Planning Center position. Unproven on real receivers.",
        settings_key="wireless_mics",
        section="Sound",
        traffic=(Traffic("wireless_mics", "Wireless mic receivers (TCP)"),),
        uses=("planning_center",),
    )
    defaults = {"receivers": [], "manual": [], "assign": {}, "low_battery": 20, "weak_rf_dbm": -85}
    admin_pages = (AdminPage(
        id="wirelessmics", title="Wireless mics", template="wireless_mics.html",
        icon='<rect x="6" y="1.5" width="4" height="7" rx="2"/><path d="M4 7.5a4 4 0 0 0 8 0M8 11.5v3"/>'),)
    admin_scripts = ("wireless_mics.js",)
    admin_styles = ("wireless_mics.css",)
    route_prefixes = ("/api/admin/wireless-mics",)
    permissions = (
        Permission("wireless_mics.view", "See the wireless mics' battery, signal and positions", "Wireless mics", "viewer"),
        Permission("wireless_mics.manage", "Change the receivers, manual mics and positions", "Wireless mics", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import modules
        modules.listen("directory.changed", self._directory_changed)

    def detach(self) -> None:
        from ... import modules

        modules.unlisten("directory.changed", self._directory_changed)

    @staticmethod
    def _directory_changed(provider: str) -> None:
        from .service import service

        service.refresh_saved()

    async def start(self) -> None:
        from .service import service

        service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def health(self) -> Health:
        from .service import service

        return Health(*service.health())

    def network_changed(self, resolved) -> None:
        import asyncio

        from .service import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="wireless-mics-network")

    async def test(self) -> dict:
        from .service import service

        snap = service.snapshot()
        if not snap["receivers"]:
            return {"ok": False, "detail": "Add a receiver first."}
        down = [r["name"] for r in snap["receivers"] if not r["connected"]]
        if down:
            return {"ok": False, "detail": "Not connected: " + ", ".join(down)}
        return {"ok": True, "detail": "Every receiver is connected."}


MODULE = WirelessMics()
