"""Signage: web pages and YouTube (#210). A web page shown full screen, or a YouTube video or playlist, as items
in a signage playlist. A module of its own because YouTube is another product; it needs Signage on to do anything.

Items need the internet. The player skips one while its screen can't reach the site, and plays it again when it can.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission

WEB_KIND = "web_page"
YOUTUBE_KIND = "youtube"


class SignageWeb(Module):
    manifest = Manifest(
        id="signage_web",
        name="Signage: web pages and YouTube",
        version="1.1.0",
        summary="Adds web pages and YouTube videos or playlists to signage playlists. They need the internet and "
                "are skipped while a screen can't reach it.",
        settings_key="signage_web",
        section="Screens",
        guide="https://github.com/justin-small/relay#signage-web-pages-and-youtube",
        uses=("signage",),
        traffic=(Traffic("signage_web", "Signage web page checks (over the internet)"),),
    )
    defaults = {"links": []}
    admin_pages = (AdminPage(
        id="signageweb", title="Web & YouTube", template="admin.html",
        icon='<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2 10 0 12M8 2c-2 2-2 10 0 12"/>'),)
    admin_scripts = ("signageweb.js",)
    route_prefixes = ("/api/admin/signage-web",)
    permissions = (
        Permission("signage_web.view", "See the saved web pages and YouTube links", "Signage web", "viewer"),
        Permission("signage_web.manage", "Add, change and remove saved web pages and YouTube links", "Signage web",
                   "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .links import clean_stored

        return clean_stored(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import modules
        from . import sourcing

        for kind, label in ((WEB_KIND, "Web page"), (YOUTUBE_KIND, "YouTube video or playlist")):
            modules.provide("signage.source." + kind, {
                "label": label, "resolve": sourcing.resolver(kind), "describe": sourcing.describer(kind),
                "choices": sourcing.chooser(kind)})

    def detach(self) -> None:
        from ... import modules

        modules.withdraw("signage.source." + WEB_KIND)
        modules.withdraw("signage.source." + YOUTUBE_KIND)

    def health(self) -> Health:
        from ... import config
        from .. import modules

        if not modules.enabled("signage"):
            return Health("warn", "Signage is off, so these items are not shown anywhere")
        n = len(config.get()["signage_web"]["links"])
        return Health("ok", f"{n} saved link{'s' if n != 1 else ''}")


MODULE = SignageWeb()
