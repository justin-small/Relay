"""Planning Center Services (#80, #81, #122): today's plan and its LIVE state on panels, and panels that move
LIVE on. A module (#157): switched on and off on the admin page's Modules page; while off it contacts
Planning Center never and adds no route, page or panel feed.

Signed in with a Personal Access Token saved on its admin page. The token is stored whole in config.json under
"planning_center" but only whether one is saved ever reaches the browser.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission

SERVICE_STREAM = "service"
# The rundown for /screen (#148): names, mics and notes, so only while the operator has it on, and never through
# the remote access tunnel.
RUNDOWN_STREAM = "rundown"


class PlanningCenter(Module):
    manifest = Manifest(
        id="planning_center",
        name="Planning Center",
        version="1.4.0",
        summary="Shows today's service plan and its LIVE item on caption panels, lets chosen panels move "
                "Services LIVE on, and puts the full rundown (clock times, teams, song leaders and mics) on /screen.",
        settings_key="planning_center",
        section="Show",
        guide="https://github.com/justin-small/relay#planning-center-services-live",
        uses=("signage",),
        traffic=(Traffic("planning_center", "Planning Center (over the internet)"),),
    )
    secret_settings = ("secret",)
    defaults = {"app_id": "", "secret": "", "service_types": [], "plan_overrides": {}, "show_notes": False,
                "rundown": {}, "signage": {}}
    admin_pages = (AdminPage(
        id="planningcenter", title="Planning Center", template="admin.html",
        icon='<path d="M3 13V6l5-4 5 4v7zM6.5 13V9h3v4"/>'),)
    admin_scripts = ("planningcenter.js", "pcrundown.js", "pcchannels.js", "pcsignage.js")
    admin_styles = ("planningcenter.css",)
    route_prefixes = ("/api/admin/planning-center", "/api/panels/{panel_id}/service/")
    permissions = (
        Permission("planning_center.view", "See the Planning Center setup and plan", "Planning Center", "viewer"),
        Permission("planning_center.control", "Choose which plan Relay follows today", "Planning Center", "operator"),
        Permission("planning_center.manage", "Change the Planning Center setup", "Planning Center", "admin"),
    )
    streams = (SERVICE_STREAM, RUNDOWN_STREAM)

    def clean(self, raw: Any) -> dict:
        from .planningcenter import clean_stored

        return clean_stored(raw)

    def public(self, settings: dict) -> dict:
        # The ID and secret are never sent back, only whether a token is saved.
        return {
            "connected": bool(settings.get("app_id") and settings.get("secret")),
            "service_types": list(settings.get("service_types") or []),
            "show_notes": bool(settings.get("show_notes")),
            "rundown_screen": bool((settings.get("rundown") or {}).get("screen")),
        }

    def configured(self, settings: dict) -> bool:
        return bool(settings.get("app_id") and settings.get("secret"))

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import config, engine as enginemod
        from ... import modules
        from . import planningcenter, signagesync
        from .pclive import follower
        from .routes import control, rundown_now
        from .servicefeed import feed

        # A service type followed or dropped reaches open panel pickers at once.
        feed.on_offered_change = enginemod.engine._push_status
        # What core and other modules ask for by name (#523). The service types a panel may be set to show or control:
        modules.provide("plan.service_types", lambda: list(config.get()["planning_center"]["service_types"]))
        modules.provide("plan.label", self._plan_label)
        modules.provide("plan.starts_at", self._service_starts_at)
        modules.provide("plan.api", planningcenter)
        modules.provide("plan.follower", follower)
        modules.provide("plan.rundown_now", rundown_now)
        modules.provide("plan.control", control)
        modules.provide("plan.feed", feed)
        # Whoever listens (crew, the ProPresenter link) hears when the followed plans change.
        if modules.announce_plan_changed not in follower.listeners:
            follower.listeners.append(modules.announce_plan_changed)
        # Plan item text slides (#209) are a kind of signage item; signage skips them while this module is off.
        modules.provide("signage.source." + signagesync.KIND, {
            "label": "Planning Center plan item", "resolve": signagesync.source_resolve,
            "describe": signagesync.source_describe, "choices": signagesync.source_choices})

    def detach(self) -> None:
        from ... import modules
        from . import signagesync
        from .pclive import follower
        from .servicefeed import feed

        feed.on_offered_change = None
        if modules.announce_plan_changed in follower.listeners:
            follower.listeners.remove(modules.announce_plan_changed)
        for name in ("plan.service_types", "plan.label", "plan.starts_at", "plan.api", "plan.follower",
                     "plan.rundown_now", "plan.control", "plan.feed"):
            modules.withdraw(name)
        modules.withdraw("signage.source." + signagesync.KIND)
        signagesync.memory.forget()

    async def start(self) -> None:
        from .pclive import follower
        from .signagesync import sync as signage_sync

        # Idle until a token is saved and a service type followed.
        follower.start()
        # Idle until plan files for signage are switched on and a service type is picked.
        signage_sync.start()

    async def stop(self) -> None:
        from .pclive import follower
        from .signagesync import sync as signage_sync

        follower.cancel()
        signage_sync.cancel()

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

    def network_changed(self, resolved) -> None:
        # Every request resolves its port again; ask Planning Center now rather than at the next poll.
        from .pclive import follower

        self._net_task = asyncio.get_running_loop().create_task(follower.poke(), name="planning-center-network")

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

    @staticmethod
    def _plan_label() -> str | None:
        from datetime import datetime

        from .pclive import follower

        for st in list(follower.tracks):
            plan = (follower.state(st) or {}).get("plan")
            if not plan:
                continue
            first = next((t["starts_at"] for t in plan["times"] if t.get("starts_at")), None)
            if first is None:
                return plan["title"]
            at = datetime.fromtimestamp(first, follower.zone or follower.fallback_zone())
            clock = f"{at.hour % 12 or 12}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"
            return f"{plan['title']} {clock}".strip()
        return ""

    @staticmethod
    def _service_starts_at() -> float | None:
        import time

        from .pclive import follower

        now, best = time.time(), None
        for st in list(follower.tracks):
            plan = (follower.state(st) or {}).get("plan") or {}
            for t in plan.get("times") or []:
                at = t.get("starts_at")
                if at and at > now and (best is None or at < best):
                    best = at
        return best

    def status(self) -> dict:
        from .servicefeed import feed

        # Planning Center service feeds a panel can pick (empty until connected).
        return {"service": feed.offered()}

    def stream(self, name: str, request: Any, params: dict) -> Any:
        if name == RUNDOWN_STREAM:
            return self._rundown_stream(request, params)
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

    def _rundown_stream(self, request: Any, params: dict) -> Any:
        import re

        from fastapi import HTTPException

        from ... import config
        from .routes import _rundown_stream, follower, via_tunnel

        if not config.get()["planning_center"]["rundown"]["screen"]:
            raise HTTPException(403, "the rundown is not switched on for /screen")
        if via_tunnel(request):
            raise HTTPException(403, "the rundown is shown on this network only")
        st = (params.get("type") or "").strip()
        if st and st not in follower.tracks:
            raise HTTPException(404, "not a followed service type")
        want = (params.get("time") or "").strip()
        return _rundown_stream(st, want if re.fullmatch(r"\d{4}", want) else "")


MODULE = PlanningCenter()
