"""UniFi (#358, #356): reads the church's UniFi Network console, read-only, with an API key (and a local user for
the live event stream). Internet health with 30 days of history, the gear's ports, PoE and firmware, Wi-Fi quality,
NDI and Dante traffic from the console's flows, its change log, and who is on the network (joins, roams, leaves),
which the crew page uses to check people in. The network map (#437) is core and takes UniFi as one of its sources.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...netroute import Resolved
from ...users import Permission


class UniFi(Module):
    manifest = Manifest(
        id="unifi",
        name="UniFi",
        summary="Reads the church's UniFi network, read-only: internet health, switch ports and PoE, Wi-Fi, NDI and "
                "Dante traffic, firmware, the console's change log, and who is on the network.",
        settings_key="unifi",
        section="Building",
        guide="https://github.com/justin-small/relay#unifi",
        version="1.1.0",
        traffic=(Traffic("unifi", "UniFi controller"),),
    )
    secret_settings = ("api_key", "password")
    network_map_adds = ("Switches, access points and the gateway, which port each device is on, link speed, PoE and "
                        "errors, and which devices carry NDI or Dante by the console's traffic flows.")
    admin_pages = (AdminPage(
        id="unifi", title="UniFi", template="unifi.html",
        icon='<circle cx="8" cy="3" r="1.6"/><circle cx="3" cy="12.5" r="1.6"/><circle cx="13" cy="12.5" r="1.6"/>'
             '<circle cx="8" cy="12.5" r="1.6"/><path d="M8 4.6v6.3M7 4.3l-3.2 6.8M9 4.3l3.2 6.8"/>'),)
    admin_scripts = ("unifi.js",)
    admin_styles = ("unifi.css",)
    route_prefixes = ("/api/admin/unifi",)
    permissions = (
        Permission("unifi.view", "See who is on the church network and when they joined", "UniFi", "viewer"),
        Permission("unifi.manage", "Connect Relay to the UniFi controller and trust its certificate", "UniFi",
                   "admin"),
    )
    defaults = {"host": "", "port": 443, "site": "default", "api_key": "", "fingerprint": "", "username": "",
                "password": "", "probe_target": "1.1.1.1", "poll_s": 30, "away_s": 300}      # service.DEFAULTS

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def public(self, settings: dict) -> dict:
        from .service import public

        return public(settings)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def network_changed(self, resolved: dict[str, Resolved]) -> None:
        from .service import service

        service.poke()

    def network_map(self) -> list[dict]:
        from .service import service

        return service.map_devices()

    def pills(self) -> list[dict]:
        from .service import service

        return service.pill()

    def health(self) -> Health:
        from .service import service

        level, detail = service.health()
        return Health(level, detail)

    async def test(self) -> dict:
        from .service import service

        await service.check()
        level, detail = service.health()
        return {"ok": level == "ok", "detail": detail}


MODULE = UniFi()
