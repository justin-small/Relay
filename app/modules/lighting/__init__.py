"""Lighting cue buttons (#377): fire a lighting program's looks from Relay's screens, panels and admin page.

This is the backbone. A small `Backend` interface (backend.py) is written once per lighting program, and the module
gives every program the same cue pages, permissions, audit trail and a `fire` hook other modules reach through the
registry (`app.modules.fire_cue`) without importing this package. No backend ships here: each program is its own
issue and registers itself in backend.py's BACKENDS.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class Lighting(Module):
    manifest = Manifest(
        id="lighting",
        name="Lighting cues",
        summary="Fires a lighting program's cues (walk-in, worship, message) from cue pages, without the lighting PC.",
        settings_key="lighting",
        guide="/docs/lighting",
        version="1.1.0",
        traffic=(Traffic("lighting", "Lighting program control (QLC+ web API)"),),
    )
    defaults = {"backend": "", "host": "", "port": 0, "password": "", "functions": False, "console": "EX", "pages": []}
    secret_settings = ("password",)
    admin_pages = (AdminPage(
        id="lighting", title="Lighting", template="lighting.html", group="Output",
        icon='<circle cx="8" cy="6" r="3"/><path d="M6.5 10.5h3M7 13h2"/>'),)
    admin_scripts = ("lighting.js",)
    route_prefixes = ("/api/admin/lighting",)
    permissions = (
        Permission("lighting.view", "See the lighting cue pages", "Lighting", "viewer"),
        Permission("lighting.control", "Fire lighting cues", "Lighting", "operator"),
        Permission("lighting.manage", "Choose the lighting program and edit the cue pages", "Lighting", "admin"),
    )

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

    # The fire hook is a hook into core, so it is set in attach() and removed in detach().
    def attach(self) -> None:
        from ... import modules as registry
        from .routes import fire

        registry.cue_hook = fire

    def detach(self) -> None:
        from ... import modules as registry

        registry.cue_hook = None

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def health(self) -> Health:
        from .service import service

        s = service.settings()
        if not s["backend"]:
            return Health("warn", "Not set up: choose the lighting program")
        if service.backend is None:
            return Health("error", "That lighting program is not available in this version of Relay")
        if service.error:
            return Health("warn", service.error)
        return Health("ok", "Ready")

    async def test(self) -> dict:
        from .service import service

        if service.backend is None:
            return {"ok": False, "detail": "Choose the lighting program first."}
        try:
            return await service.backend.test()
        except Exception:
            return {"ok": False, "detail": "The lighting program did not answer."}


MODULE = Lighting()
