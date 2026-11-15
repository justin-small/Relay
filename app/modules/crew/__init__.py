"""The crew web app (#226): an installable page for the people who serve, signed in with a core Relay account.

Crew are ordinary users (the Member role or higher) made by an admin; what the page shows follows the permissions
their role holds. It is served only on the HTTPS operator port, never on the plain-HTTP viewer port that the remote
access tunnel points at, because a service worker, the microphone and web push all need a secure context.

A module so an install that has no crew leaves it off, and so the later crew features (chat, check-in, checklists,
intercom) have one place to live. Core only lends it a page gate and the panel-only prefix rule.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from .. import AdminPage, CrewTab, Health, Manifest, Module, UserPage
from ...users import Permission

log = logging.getLogger("relay.crew")
NAME_MAX = 24
DEFAULT_NAME = "Relay crew"


class Crew(Module):
    manifest = Manifest(
        id="crew",
        name="Crew app",
        version="1.6.0",
        summary="An installable page for the crew, signed in with a Relay account, that shows the plan and hosts "
                "the other crew features. Served over HTTPS on this network only.",
        settings_key="crew",
        guide="https://github.com/justin-small/relay#crew-app",
        uses=("planning_center",),
    )
    defaults = {"name": DEFAULT_NAME, "show_plan": True, "geofence": None}
    admin_pages = (AdminPage(
        id="crew", title="Crew app", template="admin.html", group="Settings",
        icon='<circle cx="6" cy="5" r="2.5"/><circle cx="11.5" cy="6" r="2"/><path d="M1.5 13c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4M10 9.2c2.5-.3 4.5.9 4.5 3.8"/>'),)
    admin_scripts = ("crewadmin.js",)
    route_prefixes = ("/api/admin/crew", "/crew")
    panel_prefixes = ("/crew",)
    geolocation_pages = ("/crew",)
    permissions = (
        Permission("crew.use", "Open the crew page", "Crew app", "member"),
        Permission("crew.view", "See how the crew page is set up", "Crew app", "viewer"),
        Permission("crew.manage", "Change the crew page's name and what it shows", "Crew app", "admin"),
        Permission("crew.page", "Page the crew and see who has read it", "Crew app", "operator"),
        Permission("crew.chat", "Use the crew team chat", "Crew app", "member"),
        Permission("crew.checkin", "Check in at the church from the crew page", "Crew app", "member"),
        Permission("crew.checklist", "Tick your position's checklists on the crew page", "Crew app", "member"),
        Permission("crew.checklist_edit", "Write checklists for every position and name team leads", "Crew app", "operator"),
        Permission("crew.moderate", "Read every role channel and delete anyone's chat message", "Crew app", "operator"),
    )
    crew_tabs = (CrewTab("chat", "Chat", "chat.html", "crew.chat"),
                 CrewTab("checklists", "Checklists", "checklists.html", "crew.checklist"))
    crew_scripts = ("chat.js", "checklists.js")
    user_pages = (UserPage("Crew", "/crew", "crew.use", "The plan and your team's tools"),)

    def clean(self, raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        name = raw.get("name")
        name = " ".join(name.split())[:NAME_MAX] if isinstance(name, str) else ""
        from .checkin import clean_geofence

        try:
            fence = clean_geofence(raw.get("geofence"))
        except ValueError:
            fence = None         # a hand edit that does not make sense is no geofence
        return {"name": name or DEFAULT_NAME, "show_plan": raw.get("show_plan") is not False, "geofence": fence}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def __init__(self) -> None:
        self._seen = ""
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        from . import paging

        paging.pending = True      # a page sent before a restart may still be buzzing
        self._task = asyncio.create_task(self._pager(), name="crew-pager")

    async def stop(self) -> None:
        from .chat import hub

        hub.drop_all()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _pager(self) -> None:
        """Fire due pushes once a second. Evaluated here, on the Relay host, never by a phone."""
        from . import calltimes, paging

        beat = 0
        while True:
            try:
                await asyncio.to_thread(paging.tick)
                if beat % 5 == 0:         # call times have a minute's grace, so every five seconds is plenty
                    await asyncio.to_thread(calltimes.run)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("crew: paging tick failed", exc_info=True)
            beat += 1
            await asyncio.sleep(1)

    def _scan(self) -> None:
        """Match accounts to the people on the followed plans (people.py). Blocking: run off the loop."""
        from ..planning_center.pclive import follower
        from . import people

        found: dict[str, dict] = {}
        for tr in follower.tracks.values():
            for m in tr.team:
                if m.get("person"):
                    found[m["person"]] = {"id": m["person"], "name": m["name"]}
        sig = repr(sorted((i, p["name"]) for i, p in found.items()))
        if sig == self._seen:
            return
        people.scan(list(found.values()))
        self._seen = sig

    def _team_changed(self) -> None:
        """Planning Center's follower saw something change: look again when the people did."""
        async def go() -> None:
            try:
                await asyncio.to_thread(self._scan)
            except Exception:
                log.warning("crew: could not match names to Planning Center", exc_info=True)

        try:
            asyncio.get_running_loop().create_task(go())
        except RuntimeError:
            pass

    def attach(self) -> None:
        from ... import users
        from ..planning_center.pclive import follower
        from .chat import hub

        if self._team_changed not in follower.listeners:
            follower.listeners.append(self._team_changed)
        if hub.signed_out not in users.signed_out_hooks:
            users.signed_out_hooks.append(hub.signed_out)

    def detach(self) -> None:
        from ... import users
        from ..planning_center.pclive import follower
        from .chat import hub

        if hub.signed_out in users.signed_out_hooks:
            users.signed_out_hooks.remove(hub.signed_out)

        if self._team_changed in follower.listeners:
            follower.listeners.remove(self._team_changed)
        self._seen = ""

    def routers(self) -> list:
        from .routes import router

        return [router]

    def health(self) -> Health:
        return Health("ok", "The crew page is at /crew on the HTTPS port")


MODULE = Crew()
