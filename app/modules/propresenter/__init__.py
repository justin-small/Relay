"""ProPresenter (#138): follow the slides, put the lyrics and timers on panels and screens, control ProPresenter
from the admin page, the slide view and lobby rotation, slide-note tags, and the link to Planning Center (follow the
plan, ProPresenter drives Services LIVE). A module (#157): switched on and off on the admin page's Modules page;
while off it contacts ProPresenter never and adds no route, page, stream or panel feed.

The link to Planning Center (pclink.py) works only while the Planning Center module is on too.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module

LYRICS_STREAM = "lyrics"
TIMERS_STREAM = "pptimers"


class ProPresenter(Module):
    manifest = Manifest(
        id="propresenter",
        name="ProPresenter",
        version="1.0.0",
        summary="Follows ProPresenter's slides: lyrics and timers on panels and screens, control from this page, "
                "the slide view and lobby rotation, slide-note tags, and the link to Planning Center.",
        settings_key="propresenter",
        guide="https://github.com/justin-small/relay#propresenter",
        uses=("planning_center",),
    )
    admin_pages = (
        AdminPage(id="ppcontrol", title="ProPresenter control", template="control.html", group="Output",
                  icon='<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M6.5 5l3 1.5-3 1.5z" '
                       'fill="currentColor"/><path d="M4 13.5h8"/>'),
        AdminPage(id="pplive", title="Slide view and lobby", template="live.html", group="Output",
                  icon='<rect x="1.5" y="3" width="9" height="6.5" rx="1"/><rect x="8" y="7" width="6.5" '
                       'height="5.5" rx="1" fill="#0a0b0d"/><rect x="8" y="7" width="6.5" height="5.5" rx="1"/>'),
        AdminPage(id="propresenter", title="ProPresenter", template="settings.html", group="Automation",
                  icon='<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M5 13.5h6M8 10.5v3"/>'),
    )
    admin_scripts = ("propresenter.js", "slideview.js", "ppcontrol.js", "pplive.js", "pclink.js")
    route_prefixes = ("/api/admin/propresenter", "/api/stage/live", "/stage")
    streams = (LYRICS_STREAM, TIMERS_STREAM)

    @property
    def defaults(self) -> dict:
        from .propresenter import default_settings

        return default_settings()

    def clean(self, raw: Any) -> dict:
        from .propresenter import clean_stored

        return clean_stored(raw)

    def configured(self, settings: dict) -> bool:
        # Switched on, or an address entered: the operator had started setting it up.
        return bool(settings.get("enabled") or settings.get("host"))

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import alerts as alertsmod
        from ...panels import registry as panel_registry
        from ..planning_center.routes import control as pc_control
        from ..planning_center.servicefeed import feed as service_feed
        from . import propresenter, slidetags
        from .pclink import link as pc_link
        from .propresenter import follower, timers_feed
        from .tagactions import ACTIONS

        # An alert flashing on the panels is shown on ProPresenter's stage screens too.
        alertsmod.alerts.on_stage = lambda alert_id, text: (follower.alert_ended(alert_id) if text is None
                                                            else follower.alert_started(alert_id, text))
        slidetags.tags.actions = dict(ACTIONS)
        # Panels: the lyrics as a feed, and each panel's own timer settings.
        panel_registry.lyrics_offered = lambda: propresenter.panel_offers()["lyrics"]
        panel_registry.on_timers_change = timers_feed.refresh
        timers_feed.panel_settings = panel_registry.timer_settings
        # The Planning Center link: ProPresenter moves Services LIVE through the same press path as a panel, and tells
        # the Service feed which plan item it has up. Inert while the Planning Center module is off.
        pc_link.pc_control = pc_control
        pc_link.on_change = service_feed.refresh
        service_feed.extra = pc_link.feed_extra

    def detach(self) -> None:
        from ... import alerts as alertsmod
        from ...panels import registry as panel_registry
        from ..planning_center.servicefeed import feed as service_feed
        from . import slidetags
        from .pclink import link as pc_link
        from .propresenter import timers_feed

        alertsmod.alerts.on_stage = None
        slidetags.tags.actions = {}
        panel_registry.lyrics_offered = lambda: False
        panel_registry.on_timers_change = lambda: None
        timers_feed.panel_settings = lambda pid: None
        pc_link.pc_control = None
        pc_link.on_change = None
        service_feed.extra = None

    async def start(self) -> None:
        from .pclink import link as pc_link
        from .pplobby import lobby
        from .propresenter import follower

        # Idle until an address is entered and ProPresenter is switched on on its page.
        follower.start()
        # Idle until the Planning Center link is switched on (follow the plan, or ProPresenter drives LIVE).
        pc_link.start()
        # Idle until the operator presses Start on the lobby rotation (never started by Relay itself).
        lobby.start()

    async def stop(self) -> None:
        from .pclink import link as pc_link
        from .pplobby import lobby
        from .propresenter import follower

        follower.cancel()
        pc_link.cancel()
        lobby.cancel()

    def health(self) -> Health:
        from .propresenter import follower

        s = follower.state()
        if not s["configured"]:
            return Health("warn", "Not set up: enter the address and switch it on")
        if s["connected"]:
            return Health("ok", "Connected" + (f" to ProPresenter {s['version']}" if s.get("version") else ""))
        if s["connecting"]:
            return Health("warn", "Connecting")
        return Health("error", s["error"] or "Not connected")

    async def test(self) -> dict:
        from ... import config
        from . import propresenter
        from .propresenter import follower

        saved = config.get()["propresenter"]
        if not saved["host"] or not saved["port"]:
            return {"ok": False, "detail": "Enter the machine's address and ProPresenter's port first."}
        client = propresenter.Client(saved["host"], saved["port"])
        try:
            info = await follower.call(client.version)
        except propresenter.ProError:
            return {"ok": False, "detail": "Could not reach ProPresenter at the saved address and port."}
        name = info.get("name") if isinstance(info, dict) else ""
        return {"ok": True, "detail": "Connected" + (f" to {name}." if name else ".")}

    def status(self) -> dict:
        from . import propresenter

        # ProPresenter feeds a panel can pick: the slide's lyrics, while the operator has them on for panels, and the
        # timers. Both false until ProPresenter is set up.
        return {"propresenter": propresenter.panel_offers()}

    def stream(self, name: str, request: Any, params: dict) -> Any:
        from fastapi import HTTPException

        from ... import panels
        from . import propresenter
        from .propresenter import lyrics_feed, timers_feed
        from .routes import _lyrics_stream, _timers_stream

        if name == LYRICS_STREAM:
            audience = params.get("for") or "viewer"
            if audience not in propresenter.AUDIENCES:
                raise HTTPException(400, "unknown audience")
            # Licensed lyrics: each audience is the operator's switch, and the phone viewer's is off by default.
            if not lyrics_feed.allowed(audience):
                raise HTTPException(403, "the lyrics feed is not switched on for this audience")
            return _lyrics_stream(audience)
        if name == TIMERS_STREAM:
            # Timers are not licensed text, so there is no audience switch: the stream is open while ProPresenter is
            # set up (and says `enabled: false` otherwise). `panel` selects that panel's own settings.
            if params.get("for", "panel") != "panel":
                raise HTTPException(400, "unknown audience")
            pid = params.get("panel") or ""
            if pid and not panels.valid_id(pid):
                raise HTTPException(400, "not a panel id")
            if not timers_feed.allowed():
                raise HTTPException(403, "ProPresenter is not set up")
            return _timers_stream(pid)
        return None


MODULE = ProPresenter()
