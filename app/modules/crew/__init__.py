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

from .. import AdminPage, CrewTab, Health, Listener, Manifest, Module, UserPage, Traffic
from ...users import Permission

log = logging.getLogger("relay.crew")
NAME_MAX = 24
DEFAULT_NAME = "Relay crew"
JOIN_PORT = 8095          # the plain-HTTP "join" page the QR code points at (#236)


class Crew(Module):
    manifest = Manifest(
        id="crew",
        name="Crew app",
        version="1.11.0",
        summary="An installable page for the crew, signed in with a Relay account, that shows the plan and hosts "
                "the other crew features. Served over HTTPS on this network only.",
        settings_key="crew",
        section="Crew",
        guide="https://github.com/justin-small/relay#crew-app",
        uses=("planning_center", "unifi"),
        traffic=(Traffic("crew.push", "Crew notifications (web push, over the internet)"),),
    )
    defaults = {"name": DEFAULT_NAME, "show_plan": True, "geofence": None, "embed_hosts": None,
                "join_port": JOIN_PORT}
    admin_pages = (AdminPage(
        id="crew", title="Crew app", template="admin.html",
        icon='<circle cx="6" cy="5" r="2.5"/><circle cx="11.5" cy="6" r="2"/><path d="M1.5 13c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4M10 9.2c2.5-.3 4.5.9 4.5 3.8"/>'),)
    admin_scripts = ("crewadmin.js",)
    admin_styles = ("crewadmin.css",)
    route_prefixes = ("/api/admin/crew", "/crew", "/crew-join")
    listeners = (Listener(name="join", label="the crew join page", port_setting="join_port", default_port=JOIN_PORT,
                          prefixes=("/crew-join",), traffic="crew"),)  # phones join on the crew network
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
        Permission("crew.resources", "See your position's resources on the crew page", "Crew app", "member"),
        Permission("crew.resources_edit", "Add resources for every position", "Crew app", "operator"),
        Permission("crew.activity", "See who did what on the crew page", "Crew app", "operator"),
        Permission("crew.moderate", "Read every role channel and delete anyone's chat message", "Crew app", "operator"),
    )
    crew_tabs = (CrewTab("chat", "Chat", "chat.html", "crew.chat"),
                 CrewTab("checklists", "Checklists", "checklists.html", "crew.checklist"),
                 CrewTab("resources", "Resources", "resources.html", "crew.resources"),
                 CrewTab("activity", "Activity", "activity.html", "crew.activity"))
    crew_scripts = ("chat.js", "checklists.js", "resources.js", "activity.js")
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
        from .resources import clean_hosts

        try:
            port = int(raw.get("join_port") or JOIN_PORT)
        except (TypeError, ValueError):
            port = JOIN_PORT
        return {"name": name or DEFAULT_NAME, "show_plan": raw.get("show_plan") is not False, "geofence": fence,
                "embed_hosts": clean_hosts(raw.get("embed_hosts")),
                "join_port": port if 1024 <= port <= 65535 else JOIN_PORT}

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
                if beat % 15 == 0:        # UniFi reads the controller every 30 seconds by default
                    await asyncio.to_thread(self._wifi)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("crew: paging tick failed", exc_info=True)
            beat += 1
            await asyncio.sleep(1)

    def _wifi(self) -> None:
        """Wi-Fi check-in (#356): mark arrived everyone whose device the UniFi module sees on the network. Does
        nothing while UniFi is off. Blocking: run off the loop."""
        import time

        from ... import audit, users
        from ... import modules
        from . import calltimes, checkin, devices

        service = modules.using("unifi.service")
        if service is None:
            return
        clients = service.presence.online()
        if not clients:
            return
        tracks, zone = calltimes._state()
        now = time.time()
        for uid in devices.arrive_present(clients, checkin.plan_key(tracks, zone, now), now):
            audit.record("crew.checkin", "Checked in at the church", actor=users.get(uid),
                         detail={"method": "wifi"})

    def _scan(self) -> None:
        """Match accounts to the people on the followed plans (people.py). Blocking: run off the loop."""
        from ... import directory
        from . import people

        found = directory.people(directory.PCO)         # the people on the followed plans; [] while Planning Center is off
        sig = repr(sorted((p["id"], p["name"]) for p in found))
        if not found or sig == self._seen:
            return
        people.scan(found)
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
        from ... import modules, users
        from .chat import hub

        modules.listen("plan.changed", self._team_changed)
        if hub.signed_out not in users.signed_out_hooks:
            users.signed_out_hooks.append(hub.signed_out)

    def detach(self) -> None:
        from ... import modules, users
        from .chat import hub

        if hub.signed_out in users.signed_out_hooks:
            users.signed_out_hooks.remove(hub.signed_out)

        modules.unlisten("plan.changed", self._team_changed)
        self._seen = ""

    def routers(self) -> list:
        from .routes import router

        return [router]

    def health(self) -> Health:
        return Health("ok", "The crew page is at /crew on the HTTPS port")


MODULE = Crew()
