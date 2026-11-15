"""The crew web app (#226): an installable page for the people who serve, signed in with a core Relay account.

Crew are ordinary users (the Member role or higher) made by an admin; what the page shows follows the permissions
their role holds. It is served only on the HTTPS operator port, never on the plain-HTTP viewer port that the remote
access tunnel points at, because a service worker, the microphone and web push all need a secure context.

A module so an install that has no crew leaves it off, and so the later crew features (chat, check-in, checklists,
intercom) have one place to live. Core only lends it a page gate and the panel-only prefix rule.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, UserPage
from ...users import Permission

NAME_MAX = 24
DEFAULT_NAME = "Relay crew"


class Crew(Module):
    manifest = Manifest(
        id="crew",
        name="Crew app",
        version="1.0.0",
        summary="An installable page for the crew, signed in with a Relay account, that shows the plan and hosts "
                "the other crew features. Served over HTTPS on this network only.",
        settings_key="crew",
        guide="https://github.com/justin-small/relay#crew-app",
        uses=("planning_center",),
    )
    defaults = {"name": DEFAULT_NAME, "show_plan": True}
    admin_pages = (AdminPage(
        id="crew", title="Crew app", template="admin.html", group="Settings",
        icon='<circle cx="6" cy="5" r="2.5"/><circle cx="11.5" cy="6" r="2"/><path d="M1.5 13c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4M10 9.2c2.5-.3 4.5.9 4.5 3.8"/>'),)
    admin_scripts = ("crewadmin.js",)
    route_prefixes = ("/api/admin/crew", "/crew")
    panel_prefixes = ("/crew",)
    permissions = (
        Permission("crew.use", "Open the crew page", "Crew app", "member"),
        Permission("crew.view", "See how the crew page is set up", "Crew app", "viewer"),
        Permission("crew.manage", "Change the crew page's name and what it shows", "Crew app", "admin"),
    )
    user_pages = (UserPage("Crew", "/crew", "crew.use", "The plan and your team's tools"),)

    def clean(self, raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        name = raw.get("name")
        name = " ".join(name.split())[:NAME_MAX] if isinstance(name, str) else ""
        return {"name": name or DEFAULT_NAME, "show_plan": raw.get("show_plan") is not False}

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def health(self) -> Health:
        return Health("ok", "The crew page is at /crew on the HTTPS port")


MODULE = Crew()
