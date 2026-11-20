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
        section="Show",
        guide="/docs/lighting",
        version="1.3.0",
        traffic=(Traffic("lighting", "Lighting program control (QLC+ web API)"),),
    )
    defaults = {"programs": [], "pages": []}
    secret_settings = ("programs",)
    admin_pages = (AdminPage(
        id="lighting", title="Lighting", template="lighting.html",
        icon='<circle cx="8" cy="6" r="3"/><path d="M6.5 10.5h3M7 13h2"/>'),)
    admin_scripts = ("lighting.js",)
    admin_styles = ("lighting.css",)
    route_prefixes = ("/api/admin/lighting", "/lighting")
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

    # The cue and its screen page are provided to core in attach() and removed in detach().
    def attach(self) -> None:
        from ... import modules as registry
        from .routes import fire, screen_page

        registry.provide("lighting.cues", fire)
        registry.provide("screen_pages.lighting", screen_page)

    def detach(self) -> None:
        from ... import modules as registry

        registry.withdraw("lighting.cues")
        registry.withdraw("screen_pages.lighting")

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    # ------------------------------------------------------------------ boards (#156)
    def board_widgets(self) -> list[dict]:
        """A cue button for boards: one palette entry per cue on a cue page (only those may be fired from a screen)."""
        from .service import CUE_STYLE, service

        seen: dict[str, dict] = {}
        for p in service.settings()["pages"]:
            for c in p["cues"]:
                seen.setdefault(c["id"], {"name": c["name"], "style": c["style"]})
        return [{"type": "lighting.cue", "name": "Lighting cue", "group": "Lighting", "kind": "button", "w": 2, "h": 1,
                 "style": CUE_STYLE, "press_permission": "lighting.control",
                 "fields": [{"key": "cue", "label": "Cue (from a cue page)", "type": "select",
                             "options": [[cid, v["name"]] for cid, v in seen.items()]},
                            {"key": "_", "type": "note", "label": "A press is sent once and never retried. Pressing "
                             "needs someone signed in who may fire lighting cues."}],
                 "presets": [{"name": v["name"], "props": {"cue": cid}, "style": v["style"]} for cid, v in seen.items()]}]

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        from .service import service

        lit = await service.states()
        if not lit:
            return {}
        return {i["id"]: {"lit": bool(lit.get((i.get("props") or {}).get("cue")))} for i in items}

    async def board_press(self, item: dict, request) -> dict:
        from .routes import fire

        cue = (item.get("props") or {}).get("cue")
        if not isinstance(cue, str) or not cue:
            return {"ok": False, "error": "Choose the cue for this button."}
        r = await fire(cue, request)
        return r if r.get("ok") else {"ok": False, "error": "The lighting program did not take that press."}

    def health(self) -> Health:
        from .service import service

        programs = service.programs()
        if not programs:
            return Health("warn", "Not set up: choose the lighting program")
        missing = [p["name"] for p in programs if p["id"] not in service.backends]
        if missing:
            return Health("error", f"Not available in this version of Relay: {', '.join(missing)}")
        if service.error:
            return Health("warn", service.error)
        return Health("ok", "Ready" if len(programs) == 1 else f"{len(programs)} programs ready")

    async def test(self) -> dict:
        from .service import service

        from .routes import admin_test

        return await admin_test(None)


MODULE = Lighting()
