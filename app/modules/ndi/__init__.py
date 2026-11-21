"""NDI (#294): finds NDI sources on the network, lists them in admin, and gives other modules a shared receiver pool.

The base every other NDI piece (viewer, multiview, router, outputs) builds on. Sources come from mDNS (plus extra
addresses and groups); receivers are pooled, one per source however many listeners, and torn down when the last one
leaves. Other modules reach it through core's `ndi_sources`, `ndi_subscribe` and `ndi_register_output`
(app/modules/__init__.py), set in attach() and cleared in detach(), so none of them imports this package.
The NDI runtime is installed separately (runtime.py): without it the module says so and stays off.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class Ndi(Module):
    manifest = Manifest(
        id="ndi",
        name="NDI",
        summary="Finds NDI video sources on the network (cameras, converters, software) and shares one receiver per "
                "source with everything in Relay that uses NDI.",
        settings_key="ndi",
        section="Show",
        guide="/docs/ndi",
        version="1.0.0",
        traffic=(Traffic("ndi", "NDI discovery (multicast DNS) and video"),),
        uses=("blackmagic", "obs", "tally_arbiter"),     # their tally state gives the viewer its on-air border
    )
    defaults = {"servers": [], "mdns": True, "extra_ips": [], "groups": [], "interval": 15, "view_fps": 15,
                "view_quality": 60, "view_width": 1280, "view_max": 4, "tally": {}, "multiviews": [],
                "out_on": [], "out_size": "1280x720", "out_fps": 30, "router_outputs": [], "tests": [], "tests_on": [],
                # Saved before core routes (#544); read once by router.migrate() and then empty. Kept here so config keeps them.
                "routes": [], "salvos": []}  # allow-shared: migrated into core routes
    admin_pages = (AdminPage(
        id="ndi", title="NDI", template="ndi.html",
        icon='<rect x="2" y="4" width="12" height="8" rx="1"/><path d="M5 7v2.5M5 7l2 2.5V7M10 7h1.5M10 9.5h1.5"/>'),)
    admin_scripts = ("ndi.js", "ndi-router.js", "ndi-analysis.js", "ndi-tests.js")
    admin_styles = ("ndi.css",)
    route_prefixes = ("/api/admin/ndi", "/ndi")
    panel_prefixes = ("/ndi",)       # the viewer (#296) stays on the HTTPS operator port, behind ndi.view
    permissions = (
        Permission("ndi.view", "See the NDI sources found on the network", "NDI", "viewer"),
        Permission("ndi.manage", "Scan for NDI sources and set the extra addresses and groups to search",
                   "NDI", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import audio
        from ... import modules as registry
        from . import bus, runtime
        from .service import service

        audio.register_source(bus.NdiSource(service.hook_listen))
        audio.register_output(bus.NdiOutput(runtime.load))
        registry.provide("ndi", {"sources": service.hook_sources, "subscribe": service.hook_subscribe,
                                 "register_output": service.hook_output})
        registry.provide("routes.ndi", service.router.api())
        registry.provide("gear.ndi", {"list": service.gear_list})      # its sources and outputs (app/gearfeed.py)
        try:
            service.router.migrate()
        except Exception:               # the database may not be up yet; start() tries again
            pass

    def detach(self) -> None:
        from ... import audio
        from ... import modules as registry
        from . import bus

        audio.unregister_source(bus.ID)
        audio.unregister_output(bus.ID)
        registry.withdraw("ndi")
        registry.withdraw("routes.ndi")
        registry.withdraw("gear.ndi")

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def network_changed(self, resolved) -> None:
        import asyncio

        from .service import service

        async def again() -> None:
            await service.stop()
            await service.start()

        self._net_task = asyncio.get_running_loop().create_task(again(), name="ndi-network")

    def health(self) -> Health:
        from .service import service

        if not service.running:
            return Health("warn", service.error or "Off")
        src = service.sources()
        online = sum(1 for s in src if s["online"])
        down = [x["server"] for x in service.servers if not x["reachable"]]
        if down:
            return Health("warn", "Discovery server not answering: " + ", ".join(down))
        return Health("ok", f"{online} source{'s' if online != 1 else ''} online" if src else "No sources found yet")

    async def test(self) -> dict:
        from .service import service

        if not service.running:
            return {"ok": False, "detail": service.error or "NDI is not running."}
        await service.scan()
        online = [s for s in service.sources() if s["online"]]
        return {"ok": True, "detail": f"Found {len(online)} NDI source{'s' if len(online) != 1 else ''}."}

    # ------------------------------------------------------------------ boards (#156)
    def board_widgets(self) -> list[dict]:
        from .service import service

        names = [[s["name"], s["name"]] for s in service.sources()]
        return [
            {"type": "ndi.summary", "name": "NDI sources", "group": "NDI", "kind": "lines", "w": 3, "h": 2,
             "style": {"icon": "cam", "label": "NDI"},
             "fields": [{"key": "_", "type": "note", "label": "How many NDI sources are online."}]},
            {"type": "ndi.sources", "name": "NDI source list", "group": "NDI", "kind": "list", "w": 4, "h": 3,
             "style": {"icon": "cam", "label": "NDI sources"},
             "fields": [{"key": "_", "type": "note", "label": "Every NDI source found: its name, size and rate. "
                         "Offline ones are marked."}]},
            {"type": "ndi.source", "name": "NDI source", "group": "NDI", "kind": "lines", "w": 3, "h": 2,
             "style": {"icon": "cam"},
             "fields": [{"key": "source", "label": "Source", "type": "select", "options": names, "default": ""}]},
            {"type": "ndi.view", "name": "NDI picture", "group": "NDI", "kind": "frame", "w": 4, "h": 3,
             "style": {"icon": "cam"},
             "fields": [{"key": "source", "label": "Source", "type": "select", "options": names, "default": ""},
                        {"key": "_", "type": "note", "label": "Plays the source live. Only people signed in on this "
                         "network see it (a public board shows nothing). A red or green border follows the tally when "
                         "the source has a camera number on the NDI page."}]},
        ]

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        from .service import service

        src = service.sources()
        out: dict[str, dict] = {}
        for it in items:
            t = it.get("type")
            if t == "ndi.summary":
                on = sum(1 for s in src if s["online"])
                out[it["id"]] = {"lines": [f"{on} online", f"{len(src) - on} offline" if len(src) > on else "None offline"]
                                 if src else ["No sources found"]}
            elif t == "ndi.sources":
                out[it["id"]] = {"rows": [{"text": _line(s), "current": s["online"]} for s in src]
                                 or [{"text": "No sources found"}]}
            elif t == "ndi.view":
                out[it["id"]] = _picture((it.get("props") or {}).get("source"), src)
            elif t == "ndi.source":
                name = (it.get("props") or {}).get("source")
                s = next((x for x in src if x["name"] == name), None)
                out[it["id"]] = {"lines": ([s["source"] or s["machine"], "Online" if s["online"] else "Offline", _size(s)]
                                           if s else ["Choose a source" if not name else "Not found"])}
        return out


def _picture(name: Any, src: list[dict]) -> dict:
    """What a picture tile shows: the viewer's stream address (nothing private: no addresses), a note and the tally."""
    from urllib.parse import quote

    from ...tally import tally

    s = next((x for x in src if x["name"] == name), None) if isinstance(name, str) and name else None
    if s is None:
        return {"image": "", "note": "Choose a source" if not name else "Not found", "tally": ""}
    if not s["online"]:
        return {"image": "", "note": "Offline", "tally": ""}
    from .service import service

    cam = service.settings()["tally"].get(s["name"], 0)
    c = next((x for x in tally.cameras() if x["n"] == cam), None) if cam else None
    return {"image": "/ndi/stream/" + quote(s["name"], safe=""), "note": "",
            "tally": "program" if c and c["program"] else ("preview" if c and c["preview"] else "")}


def _size(s: dict) -> str:
    if not s.get("width"):
        return ""
    return f"{s['width']}x{s['height']}" + (f" {s['fps']:g} fps" if s.get("fps") else "")


def _line(s: dict) -> str:
    return " ".join(x for x in (s["name"], _size(s), "" if s["online"] else "(offline)") if x)[:120]


MODULE = Ndi()
