"""Open Sound Meter (#149): reads the sound level an Open Sound Meter install broadcasts over UDP, and shows it on
boards and the status feed next to Relay's own meter (app/metering.py).

Open Sound Meter is a free measurement tool that many churches already run on a laptop with a measurement mic. This
module listens for its UDP broadcast (multicast or plain), publishes the readings through core's metering seam and
decides nothing itself: calibration, weighting and the packet's shape are the sender's.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class OpenSoundMeter(Module):
    manifest = Manifest(
        id="open_sound_meter",
        name="Open Sound Meter",
        version="1.0.0",
        summary="Shows the level Open Sound Meter measures in the room, next to Relay's own meter.",
        settings_key="open_sound_meter",
        guide="https://github.com/justin-small/relay#sound-level",
        traffic=(Traffic("open_sound_meter", "Open Sound Meter (UDP)"),),
    )
    defaults = {"group": "", "port": 9999, "weighting": "A", "response": "slow", "warn_db": 85.0, "limit_db": 95.0}
    admin_pages = (AdminPage(
        id="opensoundmeter", title="Open Sound Meter", template="open_sound_meter.html", group="Output",
        icon='<path d="M2 11l2-5 2 8 2-10 2 9 2-4 2 3"/>'),)
    admin_scripts = ("open_sound_meter.js",)
    route_prefixes = ("/api/admin/open-sound-meter",)
    permissions = (
        Permission("open_sound_meter.view", "See what Open Sound Meter reports", "Open Sound Meter", "viewer"),
        Permission("open_sound_meter.manage", "Change where Open Sound Meter is heard", "Open Sound Meter", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .listener import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import metering
        from .listener import SOURCE

        metering.register_source(SOURCE, "Open Sound Meter")

    def detach(self) -> None:
        from ... import metering
        from .listener import SOURCE

        metering.unregister_source(SOURCE)

    async def start(self) -> None:
        from .listener import service

        await service.start()

    async def stop(self) -> None:
        from .listener import service

        await service.stop()

    def health(self) -> Health:
        from .listener import service

        s = service.snapshot()
        if s["error"]:
            return Health("error", s["error"])
        if not s["listening"]:
            return Health("warn", "Not listening")
        if s["heard"]:
            return Health("ok", "Receiving readings")
        return Health("warn", "Listening, nothing heard yet")

    def network_changed(self, resolved) -> None:
        # Its port went away, came back or was re-chosen: listen again on where it goes now.
        import asyncio

        from .listener import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="open-sound-meter-network")

    async def test(self) -> dict:
        from .listener import service

        s = service.snapshot()
        if s["error"]:
            return {"ok": False, "detail": s["error"]}
        if s["heard"]:
            return {"ok": True, "detail": "Readings are arriving."}
        return {"ok": False, "detail": "Nothing heard yet. Check Open Sound Meter is broadcasting to this address "
                                       "and port."}


MODULE = OpenSoundMeter()
