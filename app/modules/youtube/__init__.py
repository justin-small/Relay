"""YouTube live status (#252): whether the church's channel is live, since when and how many are watching, in the
shared stream status (`app/streamstatus.py`) the status view shows. Relay signs in to the channel with the Connect
button (core's device sign-in, `app/oauth.py`, #304), read-only.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class YouTube(Module):
    manifest = Manifest(
        id="youtube",
        name="YouTube",
        summary="Shows whether the church's YouTube channel is live, for how long, and how many are watching.",
        settings_key="youtube",
        guide="https://github.com/justin-small/relay#youtube",
        version="1.0.0",
        traffic=(Traffic("youtube", "YouTube (Google sign-in and live status)"),),
    )
    secret_settings = ("account", "client_secret")
    admin_pages = (AdminPage(
        id="youtube", title="YouTube", template="youtube.html", group="Output",
        icon='<rect x="1.5" y="3.5" width="13" height="9" rx="2.5"/><path d="M6.5 6v4l3.5-2z"/>'),)
    admin_scripts = ("youtube.js",)
    route_prefixes = ("/api/admin/youtube",)
    permissions = (
        Permission("youtube.view", "See whether YouTube is live and how many are watching", "YouTube", "viewer"),
        Permission("youtube.manage", "Connect and disconnect the YouTube channel and change how often it is checked",
                   "YouTube", "admin"),
    )

    defaults = {"account": "", "client_id": "", "client_secret": "", "live_s": 60, "idle_s": 600}   # service.DEFAULTS

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

    def health(self) -> Health:
        from .service import service

        level, detail = service.health()
        return Health(level, detail)

    async def test(self) -> dict:
        from .service import ACCOUNT, service

        if not ACCOUNT.connected():
            return {"ok": False, "detail": "Not connected. Press Connect on the YouTube page."}
        await service.check()
        level, detail = service.health()
        return {"ok": level in ("ok", "idle"), "detail": detail}


MODULE = YouTube()
