"""ProdMesh RTA (#149): connects to a remote ProdMesh analyser and shows its sound level and spectrum on boards and the
status feed next to Relay's own meter (app/metering.py).

Relay connects to an analyser someone already runs, over a WebSocket. Hosting the analyser inside Relay (no separate
install) is not built: it needs ProdMesh's source and licence, which are not in this repository.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class ProdMeshRta(Module):
    manifest = Manifest(
        id="prodmesh_rta",
        name="ProdMesh RTA",
        version="1.0.0",
        summary="Shows a remote ProdMesh analyser's level, loudness and 31-band spectrum next to Relay's own meter.",
        settings_key="prodmesh_rta",
        guide="https://github.com/justin-small/relay#sound-level",
        traffic=(Traffic("prodmesh_rta", "ProdMesh RTA (WebSocket)"),),
    )
    defaults = {"url": "", "warn_db": 85.0, "limit_db": 95.0}
    admin_pages = (AdminPage(
        id="prodmeshrta", title="ProdMesh RTA", template="prodmesh_rta.html", group="Output",
        icon='<path d="M2 13V9M5 13V5M8 13V7M11 13V3M14 13V8"/>'),)
    admin_scripts = ("prodmesh_rta.js",)
    route_prefixes = ("/api/admin/prodmesh-rta",)
    permissions = (
        Permission("prodmesh_rta.view", "See the ProdMesh analyser's connection and readings", "ProdMesh RTA", "viewer"),
        Permission("prodmesh_rta.manage", "Change the ProdMesh analyser's address", "ProdMesh RTA", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .client import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import metering
        from .client import SOURCE

        metering.register_source(SOURCE, "ProdMesh RTA")

    def detach(self) -> None:
        from ... import metering
        from .client import SOURCE

        metering.unregister_source(SOURCE)

    async def start(self) -> None:
        from .client import service

        service.start()

    async def stop(self) -> None:
        from .client import service

        await service.stop()

    def health(self) -> Health:
        from .client import service

        s = service.snapshot()
        if not s["configured"]:
            return Health("warn", "Not set up: enter the analyser's address")
        if s["connected"]:
            return Health("ok", "Connected" + (f", signal {s['signal']}" if s["signal"] else ""))
        return Health("error" if s["error"] else "warn", s["error"] or "Connecting")

    def network_changed(self, resolved) -> None:
        from .client import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="prodmesh-network")

    async def test(self) -> dict:
        from .client import service

        s = service.snapshot()
        if not s["configured"]:
            return {"ok": False, "detail": "Enter the analyser's address first."}
        if s["connected"]:
            return {"ok": True, "detail": "Connected."}
        return {"ok": False, "detail": s["error"] or "Not connected yet."}


MODULE = ProdMeshRta()
