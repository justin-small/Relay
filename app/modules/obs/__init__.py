"""OBS Studio (#250): scene, streaming and recording state, output statistics, scene switching (also through studio
mode, a tablet screen and slide tags), start and stop of the stream, recording and replay buffer, audio meters and mute,
a preview on the admin page, health alerts, tally, and closed captions into the stream (#140), over obs-websocket 5.

One held connection per OBS computer (a stream PC and a record PC, say) does all of it, and each publishes its own
entry in the shared stream status (`app/streamstatus.py`) that the status view shows.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


def _addr(host: Any) -> dict:
    """A configured address as the network map's `ip` (when it is one) or `host` (a name)."""
    import ipaddress

    h = host.strip() if isinstance(host, str) else ""
    try:
        return {"ip": str(ipaddress.ip_address(h))}
    except ValueError:
        return {"host": h} if h else {}


class Obs(Module):
    manifest = Manifest(
        id="obs",
        name="OBS Studio",
        summary="Shows whether OBS is streaming and recording and how healthy it is, switches scenes, and sends "
                "captions into the stream.",
        settings_key="obs",
        section="Show",
        guide="https://github.com/justin-small/relay#obs-studio",
        version="1.1.0",
        traffic=(Traffic("obs", "OBS Studio (obs-websocket)"),),
    )
    defaults = {"machines": [], "passwords": ""}
    secret_settings = ("passwords",)
    network_map_adds = "Marks the computers it controls as running OBS Studio."
    admin_pages = (AdminPage(
        id="obs", title="OBS Studio", template="obs.html",
        icon='<circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="1.5"/>'),)
    admin_scripts = ("obs.js",)
    admin_styles = ("obs.css",)
    route_prefixes = ("/api/admin/obs", "/obs")
    permissions = (
        Permission("obs.view", "See OBS's scene, outputs and preview", "OBS Studio", "viewer"),
        Permission("obs.control", "Switch OBS scenes, start and stop the stream and recording, and mute audio",
                   "OBS Studio", "operator"),
        Permission("obs.manage", "Set up OBS computers, captions and scene screens", "OBS Studio", "admin"),
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

    # Hooks into core are set in attach() and removed in detach().
    def attach(self) -> None:
        from ... import modules as registry
        from .routes import screen_page
        from .service import attach_hooks

        attach_hooks()              # the caption sink and the scene hook (slide tags)
        registry.provide("screen_pages.obs", screen_page)

    def detach(self) -> None:
        from ... import modules as registry
        from .service import detach_hooks

        detach_hooks()
        registry.withdraw("screen_pages.obs")

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()     # also clears each machine's stream status and tally

    def network_map(self) -> list[dict]:
        from ... import config

        machines = config.get().get("obs", {}).get("machines") or []
        return [{**_addr(m.get("host")), "name": m.get("name") or "", "kind": "computer", "tags": ["OBS Studio"]}
                for m in machines if isinstance(m, dict) and m.get("host")]

    def health(self) -> Health:
        from .service import service

        level, detail = service.health()
        return Health(level, detail)

    async def test(self) -> dict:
        from .routes import test_machine
        from .service import service

        if not service.machines:
            return {"ok": False, "detail": "Add an OBS computer first."}
        results = [await test_machine(m.cfg, m.password) for m in service.machines.values()]
        return {"ok": all(r["ok"] for r in results), "detail": " ".join(r["detail"] for r in results)}


MODULE = Obs()
