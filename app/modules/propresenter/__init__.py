"""ProPresenter (#138): follow the slides, put the lyrics and timers on panels and screens, control ProPresenter
from the admin page, the slide view and lobby rotation, slide-note tags, and the link to Planning Center (follow the
plan, ProPresenter drives Services LIVE). A module (#157): switched on and off on the admin page's Modules page;
while off it contacts ProPresenter never and adds no route, page, stream or panel feed.

The link to Planning Center (pclink.py) works only while the Planning Center module is on too.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ... import netsock
from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission

LYRICS_STREAM = "lyrics"
TIMERS_STREAM = "pptimers"


def _addr(host: Any) -> dict:
    """A configured address as the network map's `ip` (when it is one) or `host` (a name)."""
    import ipaddress

    h = host.strip() if isinstance(host, str) else ""
    try:
        return {"ip": str(ipaddress.ip_address(h))}
    except ValueError:
        return {"host": h} if h else {}


class ProPresenter(Module):
    network_map_adds = "Marks the computer it controls as running ProPresenter."
    manifest = Manifest(
        id="propresenter",
        name="ProPresenter",
        version="1.2.0",
        summary="Follows ProPresenter's slides: lyrics and timers on panels and screens, control from this page, "
                "the slide view and lobby rotation, slide-note tags, and the link to Planning Center.",
        settings_key="propresenter",
        section="Show",
        guide="https://github.com/justin-small/relay#propresenter",
        uses=("planning_center", "signage"),
        traffic=(Traffic("propresenter", "ProPresenter (its network API)"),),
    )
    admin_pages = (
        AdminPage(id="ppcontrol", title="Control", template="control.html",
                  icon='<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M6.5 5l3 1.5-3 1.5z" '
                       'fill="currentColor"/><path d="M4 13.5h8"/>'),
        AdminPage(id="pplive", title="Slide view and lobby", template="live.html",
                  icon='<rect x="1.5" y="3" width="9" height="6.5" rx="1"/><rect x="8" y="7" width="6.5" '
                       'height="5.5" rx="1" fill="#0a0b0d"/><rect x="8" y="7" width="6.5" height="5.5" rx="1"/>'),
        AdminPage(id="propresenter", title="Settings", template="settings.html",
                  icon='<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M5 13.5h6M8 10.5v3"/>'),
    )
    admin_scripts = ("propresenter.js", "slideview.js", "ppcontrol.js", "pplive.js", "pclink.js")
    route_prefixes = ("/api/admin/propresenter", "/api/stage/live", "/stage")
    permissions = (
        Permission("propresenter.view", "See ProPresenter slides, playlists and status", "ProPresenter", "viewer"),
        Permission("propresenter.control", "Control ProPresenter: step, trigger, clear, timers", "ProPresenter",
                   "operator"),
        Permission("propresenter.manage", "Change the ProPresenter setup and its link to Planning Center",
                   "ProPresenter", "admin"),
    )
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
        from ..signage import schedule as signage_schedule
        from . import propresenter, signagetags, slidetags
        from .pclink import link as pc_link
        from .propresenter import follower, timers_feed
        from .tagactions import ACTIONS

        # An alert flashing on the panels is shown on ProPresenter's stage screens too.
        alertsmod.alerts.on_stage = lambda alert_id, text: (follower.alert_ended(alert_id) if text is None
                                                            else follower.alert_started(alert_id, text))
        slidetags.tags.actions = {**ACTIONS, **signagetags.ACTIONS}
        # Signage: what a tag put on a screen is the schedule's "tag" layer. The actions refuse while signage is off.
        signage_schedule.register_layer("tag", signagetags.layer)
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
        from ..signage import schedule as signage_schedule
        from . import signagetags, slidetags
        from .pclink import link as pc_link
        from .propresenter import timers_feed

        signage_schedule.unregister_layer("tag")
        signagetags.forget()
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
        from .signagesync import sync as signage_sync

        # Idle until an address is entered and ProPresenter is switched on on its page.
        follower.start()
        # Idle until the Planning Center link is switched on (follow the plan, or ProPresenter drives LIVE).
        pc_link.start()
        # Idle until the operator presses Start on the lobby rotation (never started by Relay itself).
        lobby.start()
        # Idle until announcement slides for signage are switched on and a playlist is picked.
        signage_sync.start()

    async def stop(self) -> None:
        from .pclink import link as pc_link
        from .pplobby import lobby
        from .propresenter import follower
        from .signagesync import sync as signage_sync

        follower.cancel()
        pc_link.cancel()
        lobby.cancel()
        signage_sync.cancel()

    def network_map(self) -> list[dict]:
        from ... import config

        host = (config.get().get("propresenter") or {}).get("host")
        return [{**_addr(host), "name": "ProPresenter computer", "kind": "computer", "tags": ["ProPresenter"]}] if host else []

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

    def network_changed(self, resolved) -> None:
        # Its port went away, came back or was re-chosen: open the live connection again through where it goes now.
        from .propresenter import follower

        self._net_task = asyncio.get_running_loop().create_task(follower.reconnect(), name="propresenter-network")

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
            # Names the network port when ProPresenter's traffic is routed to one (#348).
            return {"ok": False, "detail": netsock.failure(propresenter.TRAFFIC,
                                                           "Could not reach ProPresenter at the saved address and port.")}
        name = info.get("name") if isinstance(info, dict) else ""
        return {"ok": True, "detail": "Connected" + (f" to {name}." if name else ".")}

    def board_follow(self) -> dict | None:
        """What a board page set to follow ProPresenter reads: the presentation up now, the slide's group and its
        label, and names to pick from (this presentation's groups and labels, and presentations seen since start)."""
        from .propresenter import follower

        pres, idx = follower.presentation, follower.slide_index
        label = ""
        if pres and idx is not None and 0 <= idx < len(pres.get("cues") or []):
            label = str(pres["cues"][idx].get("label") or "").strip()
        name = str(pres.get("name") or "").strip() if pres else ""
        if name:
            self._seen_presentations = ([name] + [n for n in getattr(self, "_seen_presentations", []) if n != name])[:100]
        groups = list(dict.fromkeys(n for n, _ in (pres or {}).get("groups") or [] if n))
        labels = list(dict.fromkeys(str(c.get("label") or "").strip() for c in (pres or {}).get("cues") or []
                                    if str(c.get("label") or "").strip()))
        return {"name": "ProPresenter", "connected": bool(follower.up),
                "now": {"presentation": name, "group": follower.group_name(), "label": label},
                "suggest": {"presentation": list(getattr(self, "_seen_presentations", [])), "group": groups,
                            "label": labels}}

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
