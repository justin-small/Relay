"""Blackmagic (#243, #386, #432, #433): one module for the Blackmagic gear a church runs.

An ATEM switcher (tally on the camera screens; cut, take, fade and macros from the booth), a HyperDeck recorder (record,
stop, play, the clip and the time left) and a Videohub router (the routing table with labels, route changes, saved
route presets, locks). Any number of each, every one with its own connection and status; the page finds Blackmagic gear
on the LAN (mDNS, and a probe of the subnet when asked) so nobody has to type an address. See service.py.

The switcher speaks the ATEM network protocol (UDP 9910, atem_protocol.py); a HyperDeck (TCP 9993) and a Videohub
(TCP 9990) speak Blackmagic's text protocols (hyperdeck.py, videohub.py). There is nothing to install. A switcher's
tally goes into core's shared tally state, which the tally screens read. For any other switcher, use Tally Arbiter.
Before #432 this was the ATEM module: its settings, switch and network port move over by themselves.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission

KIND_TAG = {"atem": "ATEM", "hyperdeck": "HyperDeck", "videohub": "Videohub"}
KIND_NAME = {"atem": "ATEM switcher", "hyperdeck": "HyperDeck", "videohub": "Videohub"}


def _addr(host: Any) -> dict:
    """A configured address as the network map's `ip` (when it is one) or `host` (a name)."""
    import ipaddress

    h = host.strip() if isinstance(host, str) else ""
    try:
        return {"ip": str(ipaddress.ip_address(h))}
    except ValueError:
        return {"host": h} if h else {}


class Blackmagic(Module):
    manifest = Manifest(
        id="blackmagic",
        name="Blackmagic",
        summary="Works with Blackmagic ATEM switchers (tally on camera screens; cut, take, fade and macros), HyperDeck "
                "recorders (record, stop, play, clip and time left) and Videohub routers (routing, labels, presets). "
                "Finds Blackmagic gear on the network.",
        settings_key="blackmagic",
        section="Show",
        guide="https://github.com/justin-small/relay#cameras-and-tally",
        version="2.1.0",
        traffic=(Traffic("blackmagic", "Blackmagic device control (ATEM over UDP, HyperDeck and Videohub over TCP)"),),
        renamed_from="atem",
    )
    defaults = {"devices": []}
    network_map_adds = "Names each Blackmagic device it is connected to."
    admin_pages = (AdminPage(
        id="blackmagic", title="Blackmagic", template="blackmagic.html",
        icon='<rect x="2" y="4" width="12" height="8" rx="1"/><path d="M5 7h2M9 7h2M5 9.5h6"/>'),)
    admin_scripts = ("bmdraw.js", "blackmagic.js")
    admin_styles = ("blackmagic.css",)
    route_prefixes = ("/api/admin/blackmagic",)
    permissions = (
        Permission("blackmagic.view", "See Blackmagic devices' connection, inputs, tally, recording and routing",
                   "Blackmagic", "viewer"),
        Permission("blackmagic.control", "Cut, take and run macros on an ATEM, record and play on a HyperDeck, "
                   "change routes and presets on a Videohub", "Blackmagic", "operator"),
        Permission("blackmagic.manage", "Add, change and remove Blackmagic devices, look for them on the network, "
                   "rename Videohub labels, lock Videohub outputs, save presets", "Blackmagic", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_config

        return clean_config(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import modules as registry
        from .service import service

        # Core's rename flow (#543) asks for this: which channel ends this module can rename on the gear, and how.
        registry.provide("gear.blackmagic", {"can_rename": service.can_rename, "rename": service.rename_end})
        registry.provide("routes.blackmagic", service.routes_api())

    def detach(self) -> None:
        from ... import modules as registry
        from .service import service

        registry.withdraw("gear.blackmagic")
        registry.withdraw("routes.blackmagic")
        service.clear_tally()

    async def start(self) -> None:
        from .service import service

        service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def network_map(self) -> list[dict]:
        from .service import devices

        return [{**_addr(d.get("host")), "name": d.get("name") or KIND_NAME[d["kind"]], "kind": "av",
                 "tags": [KIND_TAG[d["kind"]]]} for d in devices() if d.get("host")]

    def _live(self) -> list[dict]:
        from .service import service

        return [d for d in service.snapshot()["devices"] if d["enabled"] and d["host"]]

    def health(self) -> Health:
        devs = self._live()
        if not devs:
            return Health("warn", "Not set up: add a device")
        down = [d for d in devs if not d["connected"]]
        if not down:
            if len(devs) == 1:
                return Health("ok", "Connected" + (f" to {devs[0]['product']}" if devs[0]["product"] else ""))
            return Health("ok", f"All {len(devs)} devices connected")
        text = "; ".join(f"{d['name']}: {d['error'] or 'connecting'}" for d in down)
        return Health("error" if any(d["error"] for d in down) else "warn", text)

    def network_changed(self, resolved) -> None:
        # Its port went away, came back or was re-chosen: open every connection again through where it goes now.
        from .service import service

        self._net_task = asyncio.get_running_loop().create_task(service.restart(), name="blackmagic-network")

    async def test(self) -> dict:
        devs = self._live()
        if not devs:
            return {"ok": False, "detail": "Add a device first."}
        parts = [f"{d['name']}: " + (("connected" + (f" to {d['product']}" if d["product"] else ""))
                                     if d["connected"] else (d["error"] or "not connected yet"))
                 for d in devs]
        return {"ok": all(d["connected"] for d in devs), "detail": "; ".join(parts) + "."}


MODULE = Blackmagic()
