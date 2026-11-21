"""Signal map (#262): a patch list the operator pastes (CH, NAME, PORT, SOCKET, UPSTREAM), joined with the live Dante
routing, so each patched channel shows whether it is on the network, received and resolving. The first slice is the
table; the node graph and show-file import come after. Works with Dante off (the list shows as pasted, with no live
column). It reads Dante only through `dante.routing`, never its code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import AdminPage, Manifest, Module
from ...users import Permission


class SignalMap(Module):
    manifest = Manifest(
        id="signalmap",
        name="Signal map",
        version="1.0.0",
        summary="Paste the patch list (channel, name, port, socket, upstream) and see each line against the live "
                "Dante routing: which channels are on the network, which nobody receives and which cannot resolve.",
        settings_key="signalmap",
        section="Sound",
        guide="https://github.com/justin-small/relay#signal-map",
        uses=("dante",),
    )
    defaults: dict = {}
    admin_pages = (AdminPage(
        id="signalmap", title="Signal map", template="signalmap.html",
        icon='<circle cx="3" cy="4" r="1.5"/><circle cx="3" cy="12" r="1.5"/><circle cx="13" cy="8" r="1.5"/>'
             '<path d="M4.5 4h3l3 4M4.5 12h3l3-4"/>'),)
    admin_scripts = ("signalmap.js",)
    route_prefixes = ("/api/admin/signalmap",)
    permissions = (
        Permission("signalmap.view", "See the patch list against the live Dante routing", "Signal map", "viewer"),
        Permission("signalmap.manage", "Paste, replace or remove the patch list", "Signal map", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        return {}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    async def start(self) -> None:
        from ... import db

        try:
            db.migrate("signalmap", Path(__file__).parent / "migrations")
        except db.Unavailable:
            pass


MODULE = SignalMap()
