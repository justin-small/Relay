"""Planning Center Services (#80, #81, #122): today's plan and its LIVE state on panels, and panels that move
LIVE on. A module (#157): switched on and off on the admin page's Modules page; while off it contacts
Planning Center never and adds no route, page or panel feed.

Signed in with a Personal Access Token saved on its admin page. The token is stored whole in config.json under
"planning_center" but only whether one is saved ever reaches the browser.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission

SERVICE_STREAM = "service"


class PlanningCenter(Module):
    manifest = Manifest(
        id="planning_center",
        name="Planning Center",
        version="1.0.0",
        summary="Shows today's service plan and its LIVE item on caption panels, and lets chosen panels move "
                "Services LIVE on.",
        settings_key="planning_center",
        guide="https://github.com/justin-small/relay#planning-center-services-live",
    )
    defaults = {"app_id": "", "secret": "", "service_types": [], "plan_overrides": {}, "show_notes": False}
    admin_pages = (AdminPage(
        id="planningcenter", title="Planning Center", template="admin.html", group="Automation",
        icon='<path d="M3 13V6l5-4 5 4v7zM6.5 13V9h3v4"/>'),)
    admin_scripts = ("planningcenter.js",)
    route_prefixes = ("/api/admin/planning-center", "/api/panels/{panel_id}/service/")
    permissions = (
        Permission("planning_center.view", "See the Planning Center setup and plan", "Planning Center", "viewer"),
        Permission("planning_center.control", "Choose which plan Relay follows today", "Planning Center", "operator"),
        Permission("planning_center.manage", "Change the Planning Center setup", "Planning Center", "admin"),
    )
    streams = (SERVICE_STREAM,)

    def clean(self, raw: Any) -> dict:
        from .planningcenter import clean_stored

        return clean_stored(raw)

    def public(self, settings: dict) -> dict:
        # The ID and secret are never sent back, only whether a token is saved.
        return {
            "connected": bool(settings.get("app_id") and settings.get("secret")),
            "service_types": list(settings.get("service_types") or []),
            "show_notes": bool(settings.get("show_notes")),
        }

    def configured(self, settings: dict) -> bool:
        return bool(settings.get("app_id") and settings.get("secret"))

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import config, engine as enginemod
        from ...panels import registry as panel_registry
        from .servicefeed import feed

        # A service type followed or dropped reaches open panel pickers at once.
        feed.on_offered_change = enginemod.engine._push_status
        # The service types a panel may be set to show or control.
        panel_registry.followed_service_types = lambda: list(config.get()["planning_center"]["service_types"])

    def detach(self) -> None:
        from ...panels import registry as panel_registry
        from .servicefeed import feed

        feed.on_offered_change = None
        panel_registry.followed_service_types = lambda: []

    async def start(self) -> None:
        from .pclive import follower

        # Idle until a token is saved and a service type followed.
        follower.start()

    async def stop(self) -> None:
        from .pclive import follower

        follower.cancel()

    def health(self) -> Health:
        from ... import config
        from .pclive import follower

        settings = config.get()["planning_center"]
        if not self.configured(settings):
            return Health("warn", "No token saved")
        if not settings["service_types"]:
            return Health("warn", "Connected, no service types followed")
        failing = [tr for tr in follower.tracks.values() if getattr(tr, "error", None)]
        if failing:
            return Health("error", str(failing[0].error))
        n = len(settings["service_types"])
        return Health("ok", f"Following {n} service type{'s' if n != 1 else ''}")

    async def test(self) -> dict:
        from ... import config
        from . import planningcenter as pc

        client = pc.client_from(config.get())
        if client is None:
            return {"ok": False, "detail": "Save an application ID and secret first."}
        try:
            types = await asyncio.get_running_loop().run_in_executor(None, client.service_types)
        except pc.PlanningCenterError as exc:
            return {"ok": False, "detail": pc.operator_message(exc)}
        return {"ok": True, "detail": f"Connected. The account has {len(types)} service type"
                                      f"{'s' if len(types) != 1 else ''}."}

    def status(self) -> dict:
        from .servicefeed import feed

        # Planning Center service feeds a panel can pick (empty until connected).
        return {"service": feed.offered()}

    def stream(self, name: str, request: Any, params: dict) -> Any:
        if name != SERVICE_STREAM:
            return None
        from fastapi import HTTPException

        from .routes import _service_stream
        from .servicefeed import feed

        st = (params.get("type") or "").strip()
        # Only a followed service type: the feed is no way to make Relay ask Planning Center about anything else.
        if not any(o["service_type"] == st for o in feed.offered()):
            raise HTTPException(404, "not a followed service type")
        return _service_stream(st)


MODULE = PlanningCenter()
