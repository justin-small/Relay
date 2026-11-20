"""Dante (#257): finds the Dante devices on Relay's Dante network and says whether their control port answers.

Works with Dante devices, Dante Virtual Soundcard and Dante Via that announce themselves over mDNS. It lists
each device once by its Dante name, its transmit channels, and whether Relay can reach its control port (ARC), and (from each device's multicast heartbeat) its live channel levels, and (#259,
#260) lets a permitted operator change a receive channel's subscription, lock channels Relay must never change, undo
the last change and save, compare and apply routing presets. The write packet is untested on real gear (routing.py).
Renaming and the rest of what Dante Controller does stay there. Discovery is core's (app/mdns.py);
the module adds the inventory, the reachability check and the gear it offers the network map and Channels
(`gear.dante`, app/gearfeed.py).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class Dante(Module):
    manifest = Manifest(
        id="dante",
        name="Dante",
        version="1.0.0",
        summary="Finds the Dante devices on the network (stage boxes, Dante Virtual Soundcard, Dante Via), lists "
                "their transmit channels, checks that Relay can reach each one and changes which channel "
                "each receive channel listens to, with confirmation, undo and saved presets.",
        settings_key="dante",
        section="Sound",
        guide="https://github.com/justin-small/relay#dante",
        traffic=(Traffic("dante", "Dante discovery (multicast DNS), the control-port check (UDP) and the devices' heartbeat (multicast 224.0.0.233, port 8708)", media=True),),
    )
    defaults: dict = {}
    network_map_adds = "Names each Dante device it finds and the transmit channels it announces."
    admin_pages = (AdminPage(
        id="dante", title="Dante", template="dante.html",
        icon='<rect x="2" y="3" width="12" height="4" rx="1"/><rect x="2" y="9" width="12" height="4" rx="1"/>'
             '<path d="M5 5h.01M5 11h.01"/>'),)
    admin_scripts = ("dante.js",)
    route_prefixes = ("/api/admin/dante",)
    permissions = (
        Permission("dante.view", "See the Dante devices Relay found and whether they answer", "Dante", "viewer"),
        Permission("dante.control", "Change which Dante channel each receive channel listens to, and undo it", "Dante", "operator"),
        Permission("dante.manage", "Look for Dante devices again, star channels, lock receive channels and save routing presets", "Dante", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        return {}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import modules as registry
        from .service import service

        registry.provide("gear.dante", {"list": service.gear_list})
        registry.provide("audio.input_labels", service.input_labels)

    def detach(self) -> None:
        from ... import modules as registry

        registry.withdraw("gear.dante")
        registry.withdraw("audio.input_labels")

    async def start(self) -> None:
        from ... import db
        from .service import service

        try:
            db.migrate("dante", Path(__file__).parent / "migrations")
        except db.Unavailable:
            pass  # history stays in memory until the database is back
        service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def health(self) -> Health:
        from .service import service

        s = service.snapshot()
        if s["error"]:
            return Health("error", s["error"])
        if not s["devices"]:
            return Health("warn", "Looking: no Dante devices found yet")
        if s["problems"]:
            return Health("error", s["problems"][0]["text"] + (f" (and {len(s['problems']) - 1} more)" if len(s["problems"]) > 1 else ""))
        if s["unreachable"]:
            return Health("warn", f"{s['unreachable']} of {len(s['devices'])} devices did not answer on their control port")
        return Health("ok", f"{len(s['devices'])} Dante devices found")

    def network_changed(self, resolved) -> None:
        # Its port went away, came back or was re-chosen: look again through where it goes now.
        from .service import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="dante-network")

    async def test(self) -> dict:
        from .service import service

        s = service.snapshot()
        if s["error"]:
            return {"ok": False, "detail": s["error"]}
        if not s["devices"]:
            return {"ok": False, "detail": "No Dante devices found yet. Check the Dante network port and that mDNS "
                                           "reaches this computer."}
        return {"ok": not s["unreachable"],
                "detail": f"{len(s['devices'])} devices found, {s['reachable']} answered on their control port."}


MODULE = Dante()
