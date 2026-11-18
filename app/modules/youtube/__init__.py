"""YouTube live status (#252): whether the church's channel is live, since when and how many are watching, in the
shared stream status (`app/streamstatus.py`) the status view shows. Relay signs in to the channel with the Connect
button (core's sign-in, `app/oauth.py`, #304, through relay-cloud), read-only.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class YouTube(Module):
    manifest = Manifest(
        id="youtube",
        name="YouTube",
        summary="Shows whether the church's YouTube channel is live and how many are watching, schedules its live "
                "streams ahead of time, and puts each stream's link in Planning Center Publishing.",
        settings_key="youtube",
        section="Show",
        guide="https://github.com/justin-small/relay#youtube",
        version="1.1.0",
        uses=("planning_center",),
        traffic=(Traffic("youtube", "YouTube (Google sign-in, live status and scheduling)"),),
    )
    secret_settings = ("account", "client_secret")
    admin_pages = (AdminPage(
        id="youtube", title="YouTube", template="youtube.html",
        icon='<rect x="1.5" y="3.5" width="13" height="9" rx="2.5"/><path d="M6.5 6v4l3.5-2z"/>'),)
    admin_scripts = ("youtube.js", "ytschedule.js")
    admin_styles = ("youtube.css", "ytschedule.css")
    route_prefixes = ("/api/admin/youtube",)
    permissions = (
        Permission("youtube.view", "See whether YouTube is live and how many are watching", "YouTube", "viewer"),
        Permission("youtube.publish", "Send the live stream's link to Planning Center now", "YouTube", "operator"),
        Permission("youtube.manage", "Connect the YouTube channel, schedule its live streams and link them to "
                   "Planning Center", "YouTube", "admin"),
    )

    defaults = {"account": "", "client_id": "", "client_secret": "", "live_s": 60, "idle_s": 600,
                "schedule": {"on": False, "privacy": "public", "stream_id": "", "stream_name": "",
                             "title": "{name} · {date}", "description": "", "days_ahead": 7, "plan_types": [],
                             "rules": []},
                "links": [], "created": {}}   # service.DEFAULTS

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
        from .schedule import planner
        from .service import service

        await service.start()
        await planner.start()

    async def stop(self) -> None:
        from .schedule import planner
        from .service import service

        await planner.stop()
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
