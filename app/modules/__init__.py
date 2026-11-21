"""Relay modules (#157): every integration is a module that is switched on and off on its own.

Relay's core is captions, translation, panels, alerts, schedules and recordings. Anything that talks to
another product (ProPresenter, Planning Center, mixing consoles, lighting, streams) is a module: a package
under app/modules/<id>/ whose `__init__.py` defines `MODULE`, an instance of `Module`. Modules ship with
the app; nothing is downloaded at run time. The operator switches each one on or off on
the admin page's Modules page, and a module that is off carries none of its code paths:

* its background work is never started (`start`/`stop`),
* every route it adds answers 404 (the registry mounts its routers behind `require_enabled`),
* its admin pages, nav links and scripts are not rendered,
* it adds nothing to the status, the panel feeds or the caption streams.

The direction of imports is one way: a module may import core (config, hub, engine, panels, alerts, ...);
core never imports a module. Core reaches modules only through this registry and what modules provide by name
(`provide` and `using`, below), and a module reaches another the same way: it never imports it. A module that works
with another one (the ProPresenter link to Planning Center) names it in `Manifest.uses` and must cope with
`using()` returning None while it is off. tests/test_modules.py enforces all of this; docs/modules.md is the guide.

The registry itself is core and imports each module package to read its manifest. Importing a module runs
nothing: work starts only in `start`, which is called only while the module is on.
"""
from __future__ import annotations

import contextlib
import importlib
import logging
import pkgutil
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..netroute import Resolved, Traffic
from ..users import Permission, User
from .. import users as users_mod


log = logging.getLogger("relay.modules")

HERE = Path(__file__).resolve().parent


def _suppress():
    return contextlib.suppress(Exception)
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
HEALTH_STATES = ("ok", "warn", "error", "idle")
# The headings of the module entries in the admin sidebar, in order (Manifest.section).
NAV_SECTIONS = ("Show", "Crew", "Screens", "Sound", "Building", "Modules")


@dataclass(frozen=True)
class Manifest:
    """What a module is. `id` is its package name under app/modules/ and its key under config "modules"."""
    id: str
    name: str
    version: str
    summary: str
    # The config.json key holding the module's own settings (kept where they were before modules, so an
    # upgrade moves no data).
    settings_key: str
    # Where the setup guide is: a README anchor or a docs/ path, shown next to the module's settings.
    guide: str = ""
    # Other modules this one does more with when they are on too. It asks for what they provide by name
    # (`using()`) and copes with None; it imports none of them (tests/test_shared_concepts.py).
    uses: tuple[str, ...] = ()
    # Kinds of network traffic the operator can put on a port of their own (app/netroute.py, #344). Each id is the
    # module's id or starts with "<module id>.": Traffic("dante", "Dante control and discovery", media=True).
    traffic: tuple[Traffic, ...] = ()
    # The id this module had before it was renamed. config.json's settings key, on/off switch and network routes
    # saved under the old id move to the new one when config loads, once (#432, #433: "atem" became "blackmagic").
    renamed_from: str = ""
    # The heading the module is listed under in the admin sidebar, one of NAV_SECTIONS. Each module is one entry
    # there, with its admin pages under it; one with no section is listed under "Modules".
    section: str = ""


TAB_PAGES = ("network", "signage")

# Modules that were removed: what a config saved under one of these ids kept is dropped on load (aes67, #603).
RETIRED_MODULES = ("aes67",)


@dataclass(frozen=True)
class AdminPage:
    """A page the module adds to the admin panel, rendered only while the module is on."""
    id: str            # the #hash and data-page name; unique across Relay
    title: str         # the nav label
    template: str      # a template in the module's templates/ directory
    icon: str = ""     # inner SVG for the 16x16 nav icon; the module's entry uses its first page's
    # The permission needed to see the page's nav link; empty means "<module id>.view".
    permission: str = ""
    # A core page this one is a tab of, instead of a sidebar entry of its own: "network" puts it on the Network
    # page's tab bar after core's tabs. Core renders the template in the tab's panel; the module's scripts run as
    # for any page. "signage" is the Signage module's page: it draws the tabs itself. The only core page that takes tabs is TAB_PAGES.
    tab_of: str = ""


@dataclass(frozen=True)
class UserPage:
    """A feature the module offers outside the admin panel, listed on the home page (/home) of a signed-in user
    who holds `permission`, while the module is on (#175). The route behind `path` checks the permission itself,
    through `permission_hook`, like an admin route: the list only tidies the screen."""
    title: str         # the link's name on the home page
    path: str          # the module's own page; must start with one of its route_prefixes
    permission: str    # one of the module's own permissions
    note: str = ""     # one line under the title: what it is for


@dataclass(frozen=True)
class CrewTab:
    """A tab the module adds to the crew page (/crew, #226), shown to a signed-in user who holds `permission`,
    while the module is on. The module's template is rendered inside the tab's panel and its `crew_scripts` run on
    the page. The routes the tab calls check the permission themselves, like an admin route."""
    id: str            # unique across Relay; the #hash of the tab
    title: str         # the tab's label
    template: str      # a template in the module's templates/ directory
    permission: str    # one of the module's own permissions


@dataclass(frozen=True)
class Listener:
    """A plain-HTTP port of the module's own, opened by run.py at startup while the module is on (#205). Only the
    module's routes under `prefixes` answer on it, and those routes answer on no other port, so a device pointed at
    it (a TV) reaches nothing else. The port is the module's setting `port_setting`, or `default_port`. A change
    takes effect when Relay next starts."""
    name: str                    # unique within the module
    label: str                   # what it serves, for the startup lines and the admin page ("signage screens")
    port_setting: str            # the key in the module's own settings holding the port number
    default_port: int
    prefixes: tuple[str, ...]    # each under the module's route_prefixes
    # The traffic class whose network port it listens on (app/netroute.py): a core class ("viewers", "crew") or one
    # the module declares in manifest.traffic. Unrouted, it listens on every interface as before.
    traffic: str = "viewers"


@dataclass(frozen=True)
class Health:
    state: str         # one of HEALTH_STATES
    detail: str = ""


class Module:
    """The interface every module implements. Subclasses set `manifest` and override what they need."""

    manifest: Manifest
    # Settings stored under manifest.settings_key when nothing is saved yet.
    defaults: dict[str, Any] = {}
    # Keys of those settings that are secrets: config.json keeps them sealed with the machine key (#278).
    secret_settings: tuple[str, ...] = ()
    # Admin pages, and the scripts in the module's static/ directory the admin page loads for it.
    admin_pages: tuple[AdminPage, ...] = ()
    # True: the module's pages are one sidebar entry and tabs across the top of each page, not a submenu.
    admin_tabbed: bool = False
    admin_scripts: tuple[str, ...] = ()
    # Stylesheets in static/ the admin page loads for it (#463). They place the shared controls in components.css
    # and use the tokens in tokens.css; tests/test_style_contract.py keeps literal sizes and colours out.
    admin_styles: tuple[str, ...] = ()
    # Features for accounts that never open the admin panel (a Member's home page).
    user_pages: tuple[UserPage, ...] = ()
    # Tabs on the crew page, and the scripts in the module's static/ directory that page loads for them.
    crew_tabs: tuple[CrewTab, ...] = ()
    crew_scripts: tuple[str, ...] = ()
    crew_styles: tuple[str, ...] = ()
    # Exact page paths (of any module) that may use the microphone while this module is on. Everywhere else the
    # browser is told microphone=(); a page that is not listed never gets a prompt.
    microphone_pages: tuple[str, ...] = ()
    geolocation_pages: tuple[str, ...] = ()      # pages that may ask for the phone's position (crew check-in, #231)
    # Path prefixes of every route the module adds. Core templates and scripts must not name them
    # (tests/test_modules.py), so the only way into a module is through pages it renders itself.
    route_prefixes: tuple[str, ...] = ()
    # The /stream?stream=<name> streams it serves through `stream()`.
    streams: tuple[str, ...] = ()
    # What the module's users may be allowed to do, as users.Permission("<id>.<action>", label, group, min_role).
    # Every id starts with "<module id>."; the registry registers them at discovery.
    permissions: tuple[Permission, ...] = ()
    # Ports of its own that only its routes answer on (see Listener).
    listeners: tuple[Listener, ...] = ()
    # Prefixes (each under route_prefixes) answered only on the HTTPS operator port, never on the plain-HTTP viewer
    # port, so a remote access tunnel (which points at the viewer port) never carries them. For pages that need a
    # secure context: a service worker, the microphone, web push.
    panel_prefixes: tuple[str, ...] = ()

    # ------------------------------------------------------------ settings
    def clean(self, raw: Any) -> dict:
        """The stored settings, normalised. Called on every config load, whether the module is on or not."""
        return dict(self.defaults) | (raw if isinstance(raw, dict) else {})

    def public(self, settings: dict) -> dict:
        """The settings as the admin browser may see them: strip anything secret."""
        return settings

    def configured(self, settings: dict) -> bool:
        """Whether the operator had set this integration up. Decides whether it starts on after the
        upgrade that introduced modules (and on any install with no switch saved for it yet)."""
        return False

    # ------------------------------------------------------------ routes
    def routers(self) -> list[APIRouter]:
        """The routes the module adds. The registry mounts them behind `require_enabled`."""
        return []

    # ------------------------------------------------------------ life cycle
    def attach(self) -> None:
        """Set the module's hooks into core (callables core holds, such as a panel registry hook). Synchronous
        and cheap, no background work. Called before `start`."""

    def detach(self) -> None:
        """Put every hook `attach` set back to core's default. Called after `stop`."""

    async def start(self) -> None:
        """Start background work. Called at startup while on, and when switched on, after `attach`."""

    async def stop(self) -> None:
        """Stop everything `start` began. Called when switched off and at shutdown, before `detach`."""

    def health(self) -> Health:
        return Health("idle", "")

    async def test(self) -> dict:
        """Test the connection with the saved settings: {"ok": bool, "detail": str}."""
        return {"ok": False, "detail": "This module has no connection test."}

    def network_changed(self, resolved: dict[str, Resolved]) -> None:
        """A class in `manifest.traffic` now resolves differently: its port went away, came back, changed address,
        or the operator routed it elsewhere. `resolved` has every class of the module, by id
        (app.netroute.resolve). Called only while the module is running, from the event loop: start work with
        asyncio, do not block."""

    # ------------------------------------------------------------ hooks into core
    def status(self) -> dict:
        """Keys merged into the engine status the admin page and panels read."""
        return {}

    # What this module adds to the network map (#437), one sentence for the map's Sources card, shown even while it is off;
    # "" when it adds nothing. The devices themselves come from `modules.provide("gear.<id>", {"list": fn})` (app/gearfeed.py).
    network_map_adds: str = ""

    def board_widgets(self) -> list[dict]:
        """Widgets this module adds to boards (#156), as app/boards.py describes a definition: {"type":
        "<module id>.<name>", "name", "group", "kind" (one of boards.KINDS, drawn by core), "w", "h", "style",
        "fields", "presets", "press_permission"}. A pressable widget ("kind": "button") names the permission (one this
        module declares) a signed-in person needs to press it; nobody presses it from a public board signed out.
        Called only while the module is on; read what is in memory."""
        return []

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        """Live data for this module's widgets on a board, by item id: {"lit": bool} for a button, {"lines": [...]},
        {"rows": [{"text", "current"}]}, {"on_air", "live_since", "viewers"}. `items` are only this module's own.
        Boards may be public: send nothing private, no addresses and no error text."""
        return {}

    def board_follow(self) -> dict | None:
        """What a board set to follow this module reads (#156 pages): None when the module offers nothing to follow,
        else {"name": "ProPresenter", "connected": bool, "now": {"presentation", "group", "label"} (what shows now,
        "" for none), "suggest": {"presentation": [...], "group": [...], "label": [...]}} (names the operator can
        pick). Called from the event loop every few seconds: read what is in memory."""
        return None

    async def board_press(self, item: dict, request: Any) -> dict:
        """Someone pressed one of this module's button widgets on a board: {"ok": True} or {"ok": False, "error":
        "<a fixed sentence>"}. Core has already checked the widget's press_permission. Say what happened with
        `audit.note(request, ...)` as in an admin route; core writes it with the person who pressed once it succeeded."""
        return {"ok": False, "error": "That button does nothing."}

    def pills(self) -> list[dict]:
        """Pills for the admin top bar, shown while the module is on: [{"id", "text", "count", "href"}], plus
        "level": "error" for a pill that reports a problem (drawn red)."""
        return []

    def stream(self, name: str, request: Any, params: dict) -> Any:
        """A /stream response for a stream this module owns, or None if `name` is not one of its own."""
        return None


# ------------------------------------------------------------------ what core gives modules
# A module cannot import app.main (main imports this registry), so the few things its routes need from the
# web layer are here.
HEARTBEAT_S = 15.0
# main sets this to its `require` (permission -> FastAPI dependency); a module cannot import main.
permission_hook: Callable[[str], Callable[..., Any]] | None = None
# main sets this to its Jinja2Templates; a module's own templates are named "<module id>/<file>".
templates: Any = None
# main sets this to its page gate: (request, permission) -> the signed-in User, or the response to send instead
# (sign-in form, password change, "not allowed"). Blocks (database); call it through asyncio.to_thread.
page_gate: Callable[..., Any] | None = None


# ------------------------------------------------------------------ capabilities (#523)
# One way for a module to offer something to core or to another module, and for them to ask for it: a module that is
# on provides a named capability in `attach()` (`modules.provide("lighting.cues", fire)`), and anything that wants it
# asks by name (`modules.using("lighting.cues")`) and copes with None, which is what it gets while the provider is off.
# Nothing imports another module. Turning a module off withdraws everything it provided (the registry does it after
# `detach`, and a module may call `withdraw` itself). The names Relay's own modules provide are in docs/modules.md.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)*$")
_provided: dict[str, tuple[str, Any]] = {}          # name -> (module id, what it provides)
_listeners: dict[str, list[Callable[..., Any]]] = {}


def _owner_of_caller(depth: int = 2) -> str:
    """The id of the module whose code called `provide`: the package under app/modules/ its file belongs to, else ''."""
    name = sys._getframe(depth).f_globals.get("__name__", "")
    if name.startswith(__name__ + "."):
        return name[len(__name__) + 1:].split(".", 1)[0]
    return ""


def provide(name: str, impl: Any, *, owner: str | None = None) -> None:
    """Offer `impl` (a function, an object, anything the asker needs) under `name` ("<area>" or "<area>.<what>", such as
    "ndi", "lighting.cues" or "screen_pages.obs"). The owner is the module this is called from, so turning that module off
    takes it away again; pass `owner` only from code that is not inside a module's package. Providing a name that
    another module already holds replaces it, and is logged."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"a capability is named <area> or <area>.<what> in lower case, not {name!r}")
    who = _owner_of_caller() if owner is None else owner
    held = _provided.get(name)
    if held is not None and held[0] != who:
        log.warning("capability %s: %s replaces %s", name, who or "code outside a module", held[0] or "code outside a module")
    _provided[name] = (who, impl)


def withdraw(name: str) -> None:
    """Take back what `provide` offered under `name`. Harmless when nothing is there."""
    _provided.pop(name, None)


def withdraw_all(owner: str) -> None:
    """Take back everything `owner` provided: the registry calls it when a module is switched off."""
    for name in [n for n, (who, _) in _provided.items() if who == owner]:
        del _provided[name]


def using(name: str, default: Any = None) -> Any:
    """What `name` stands for, or `default` (None) while no module that is on provides it."""
    held = _provided.get(name)
    return default if held is None else held[1]


def using_all(prefix: str) -> dict[str, Any]:
    """Every capability whose name starts with `prefix` (such as "screen_pages."), by the rest of the name."""
    return {n[len(prefix):]: impl for n, (_, impl) in sorted(_provided.items()) if n.startswith(prefix)}


def provided() -> dict[str, str]:
    """Every capability on offer, by name, with the id of the module that provides it (for tests and the guide)."""
    return {n: who for n, (who, _) in sorted(_provided.items())}


def listen(topic: str, fn: Callable[..., Any]) -> None:
    """Be called when `announce(topic, ...)` is: a module that wants to hear about another's events (the followed
    plan changed) listens in `attach()`, with no import and no matter which of the two came on first. `unlisten`
    in `detach()`. Called on the event loop; a listener must not block."""
    if fn not in _listeners.setdefault(topic, []):
        _listeners[topic].append(fn)


def unlisten(topic: str, fn: Callable[..., Any]) -> None:
    if fn in _listeners.get(topic, []):
        _listeners[topic].remove(fn)


def announce(topic: str, *args: Any) -> None:
    for fn in list(_listeners.get(topic, [])):
        try:
            fn(*args)
        except Exception:
            log.exception("listener for %s failed", topic)


def announce_plan_changed() -> None:
    """The followed plans changed (Planning Center's follower calls it): `listen("plan.changed", fn)` to hear."""
    announce("plan.changed")


async def fire_cue(cue_id: str, request: Any = None, *, by_name: bool = False, source: str = "") -> dict:
    """Fire a cue the operator put on a lighting page. {"ok": False, "error": ...} when lighting is off, the cue is
    not on a page, or the program could not do it. Pass the admin `request` to have the press in the audit trail, or
    (with no request) a `source` such as "a ProPresenter slide tag" to have it recorded as a system entry that says
    where it came from. `by_name` takes the cue's name on the page (any case) instead of the program's cue id.
    Lighting provides "lighting.cues": (cue, request, *, by_name, source) -> {"ok": bool, "error": str}."""
    hook = using("lighting.cues")
    if hook is None:
        return {"ok": False, "error": "Lighting is off."}
    return await hook(cue_id, request, by_name=by_name, source=source)


async def switch_scene(name: str, *, source: str = "") -> dict:
    """Switch every connected OBS computer that has a scene called `name` (any case) to it. {"ok": False, "error":
    ...} when OBS is off, no computer has that scene, or OBS refused. `source` ("a ProPresenter slide tag") is recorded
    as a system audit entry saying where the switch came from. OBS provides "obs.scenes": (name, *, source) -> dict."""
    hook = using("obs.scenes")
    if hook is None:
        return {"ok": False, "error": "The OBS module is off."}
    return await hook(name, source=source)


async def control_recording(command: str, value: str = "", *, source: str = "") -> dict:
    """Tell every recorder that answers to `record`, `stop`, add a `marker` (named `value`) or run an `action` (a
    command id). {"ok": False, "error": ...} when no recorder module is on or none answered. `source` ("a ProPresenter
    slide tag") is recorded as a system audit entry saying where it came from. REAPER provides "reaper.recording":
    (command, value, *, source) -> {"ok": bool, "error": str}."""
    hook = using("reaper.recording")
    if hook is None:
        return {"ok": False, "error": "The REAPER module is off."}
    return await hook(command, value, source=source)


def announce_presentation(name: str) -> None:
    """A new presentation is up in ProPresenter (#413), which calls this with its name: `listen("presentation.up", fn)`
    to hear (REAPER's song markers). Called on the event loop; a listener must not block."""
    announce("presentation.up", name)


# The NDI module provides "ndi": a dict of the functions other modules use, `sources() -> list[dict]`,
# `subscribe(source, on_frame) -> unsubscribe()` and `register_output(name) -> output` (#294). Callers use `ndi_sources`,
# `ndi_subscribe` and `ndi_register_output`, never the module.
def _ndi(name: str) -> Callable[..., Any] | None:
    hooks = using("ndi")
    return hooks.get(name) if isinstance(hooks, dict) else None


def ndi_sources() -> list[dict]:
    """The NDI sources found on the network: name, machine, source, address, groups, online, width, height, fps.
    Empty while the NDI module is off."""
    fn = _ndi("sources")
    return fn() if fn else []


def ndi_subscribe(source: str, on_frame: Callable[[dict], None]) -> Callable[[], None] | None:
    """Receive the frames of an NDI source through the module's shared receiver (one per source, however many
    listeners). `on_frame` gets {"width", "height", "fps", "fourcc", "data"} on the receiver's thread and must not
    block. Returns the function that stops listening, or None while NDI is off or the runtime is missing."""
    fn = _ndi("subscribe")
    return fn(source, on_frame) if fn else None


def ndi_register_output(name: str, width: int = 1280, height: int = 720, fps: float = 30.0) -> Any:
    """Register an NDI output called `name` (#299), sending BGRA frames of width x height at `fps`. Returns an object
    with `send({"data": bytes})` and `close()`, or None while NDI is off or the runtime is missing."""
    fn = _ndi("register_output")
    return fn(name, width, height, fps) if fn else None


# A module with a page worth sending as NDI provides "ndi.outputs.<its id>" (#299): a function returning
# [{"id", "name", "url", "alpha", "group"}], where `url` is a path on the viewer port or http://127.0.0.1:<port>/... for
# a page served on a port of its own. The NDI module lists them on its Outputs page (`using_all("ndi.outputs.")`).


def plan_label() -> str | None:
    """Today's plan as a short title ("Sunday Service 9:00 AM"), "" when there is none today, and None when no module
    that follows plans is on. Planning Center provides "plan.label": () -> str."""
    fn = using("plan.label")
    try:
        return fn() if fn else None
    except Exception:
        return None


def service_starts_at() -> float | None:
    """When the next service starts, as a Unix time, or None when no module that knows one is on. Planning Center
    provides "plan.starts_at": () -> float | None."""
    fn = using("plan.starts_at")
    try:
        return fn() if fn else None
    except Exception:
        return None


def require(perm: str) -> Callable[..., Any]:
    """FastAPI dependency for a module's admin route: the signed-in user must hold permission `perm`
    (`<module id>.view`, `<module id>.manage` or a finer one the module declares). 401 when signed out,
    403 when the role lacks it, and the same checks as every other admin route."""
    inner: list[Callable[..., Any]] = []

    def dep(request: Request) -> User:
        if permission_hook is None:
            raise HTTPException(status_code=401, detail="Sign in required")
        if not inner:
            inner.append(permission_hook(perm))
        return inner[0](request)

    dep.permission = perm          # type: ignore[attr-defined]  (tests read it)
    return dep


# `require_admin` means the core permission `modules.manage` (admin and above), which says nothing about the module's
# own pages: a module's routes should use `require("<id>.view")`, `require("<id>.manage")` or a finer permission it
# declares (`<id>.control`). It stays so a third-party module written before permissions keeps working, never open
# and never wider than an admin. Relay's own modules no longer use it (tests/test_modules.py).
require_admin = require("modules.manage")


async def read_limited(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it passes `limit` bytes -- not read
    whole first, so a client cannot make this process buffer a large upload."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "request too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def event_stream(gen: Any) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ registry
class Registry:
    def __init__(self) -> None:
        self._health_cache: tuple[float, dict[str, str]] | None = None
        self._modules: dict[str, Module] | None = None
        self._running: set[str] = set()
        # Called after a module is switched on or off (main pushes a status update).
        self.on_change: Callable[[], None] = lambda: None

    # ------------------------------------------------------------ discovery
    def all(self) -> list[Module]:
        if self._modules is None:
            self._modules = discover()
        return list(self._modules.values())

    def get(self, mid: str) -> Module | None:
        self.all()
        assert self._modules is not None
        return self._modules.get(mid)

    # ------------------------------------------------------------ switches
    def enabled(self, mid: str) -> bool:
        from .. import config

        return bool((config.get().get("modules") or {}).get(mid, {}).get("enabled"))

    def active(self) -> list[Module]:
        """The modules that are switched on, in discovery order."""
        return [m for m in self.all() if self.enabled(m.manifest.id)]

    def plan_label(self) -> str | None:
        return plan_label()

    def service_starts_at(self) -> float | None:
        return service_starts_at()

    def require_enabled(self, mid: str) -> Callable[[], Awaitable[None]]:
        """A FastAPI dependency: 404 while the module is off, exactly as if its routes were not there."""
        async def dep() -> None:
            if not self.enabled(mid):
                raise HTTPException(404, "Not Found")
        return dep

    async def set_enabled(self, mid: str, on: bool) -> None:
        from .. import config

        mod = self.get(mid)
        if mod is None:
            raise KeyError(mid)
        config.save({"modules": {mid: {"enabled": bool(on)}}})
        if on:
            await self._start(mod)
        else:
            await self._stop(mod)
        self.on_change()

    # ------------------------------------------------------------ life cycle
    def settle(self) -> None:
        """Write the switch of any module config.json has none for yet, as `clean_config` worked it out
        (on if the integration was already set up). Called once at startup, so a later change to the
        integration's own settings never flips a module on by itself."""
        from .. import config

        stored = config.read_raw().get("modules")
        stored = stored if isinstance(stored, dict) else {}
        if any(m.manifest.id not in stored for m in self.all()):
            config.save({"modules": config.get()["modules"]})

    async def start_enabled(self) -> None:
        for mod in self.active():
            await self._start(mod)

    async def stop_all(self) -> None:
        """At shutdown: stop the background work. The hooks stay attached, since nothing runs after this (and a
        test's app, started and stopped around a request, keeps the hooks it attached)."""
        for mod in self.all():
            await self._stop(mod, detach=False)

    async def _start(self, mod: Module) -> None:
        mid = mod.manifest.id
        if mid in self._running:
            return
        try:
            mod.attach()
            await mod.start()
        except Exception:
            log.exception("module %s failed to start", mid)
            with _suppress():
                mod.detach()
            withdraw_all(mid)
            return
        self._running.add(mid)
        log.info("Module on: %s %s", mod.manifest.name, mod.manifest.version)

    async def _stop(self, mod: Module, detach: bool = True) -> None:
        mid = mod.manifest.id
        if mid not in self._running:
            return
        self._running.discard(mid)
        try:
            await mod.stop()
        except Exception:
            log.exception("module %s failed to stop", mid)
        if detach:
            with _suppress():
                mod.detach()
            withdraw_all(mid)
        log.info("Module off: %s", mod.manifest.name)

    def attach(self, mid: str) -> None:
        """Only the hooks of a module, without starting it: for tests, which drive its background work
        themselves."""
        mod = self.get(mid)
        assert mod is not None, mid
        mod.attach()

    def running(self, mid: str) -> bool:
        return mid in self._running

    # ------------------------------------------------------------ hooks
    def status(self) -> dict:
        out: dict = {}
        for mod in self.active():
            try:
                out.update(mod.status())
            except Exception:
                log.exception("module %s status failed", mod.manifest.id)
        return out

    def board_widgets(self) -> list[dict]:
        """Every switched-on, running module's board widgets (#156), cleaned (app/boards.py). A definition whose type
        does not start with the module's id, or whose press permission the module does not declare, is left out."""
        from .. import boards

        out: list[dict] = []
        for mod in self.active():
            if not self.running(mod.manifest.id):
                continue
            mid = mod.manifest.id
            try:
                raw = mod.board_widgets()
            except Exception:
                log.exception("module %s: board_widgets failed", mid)
                continue
            own = {p.id for p in mod.permissions}
            for r in raw if isinstance(raw, list) else []:
                d = boards.clean_definition(r, owner=mid)
                if d is None or (d["press_permission"] and d["press_permission"] not in own):
                    log.warning("module %s: a board widget was left out (bad type or permission)", mid)
                    continue
                out.append(d)
        return out

    def board_follow(self) -> dict[str, dict]:
        """Every switched-on, running module's follow source for board pages, by module id, bounded."""
        out: dict[str, dict] = {}
        for mod in self.active():
            mid = mod.manifest.id
            if not self.running(mid):
                continue
            try:
                got = mod.board_follow()
            except Exception:
                log.exception("module %s: board_follow failed", mid)
                continue
            if not isinstance(got, dict):
                continue
            now = got.get("now") if isinstance(got.get("now"), dict) else {}
            sug = got.get("suggest") if isinstance(got.get("suggest"), dict) else {}
            keys = ("presentation", "group", "label")
            out[mid] = {"name": str(got.get("name") or mid)[:40], "connected": got.get("connected") is True,
                        "now": {k: str(now.get(k) or "")[:80] for k in keys},
                        "suggest": {k: [str(v)[:80] for v in (sug.get(k) if isinstance(sug.get(k), list) else [])
                                        if isinstance(v, str) and v.strip()][:200] for k in keys}}
        return out

    def board_owner(self, wtype: str) -> Module | None:
        """The running module whose board widget type this is, else None."""
        mid = wtype.split(".", 1)[0] if "." in wtype else ""
        mod = self.get(mid) if mid else None
        return mod if mod is not None and self.enabled(mid) and self.running(mid) else None

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        """Live data for every module widget in `items`, by item id. A module that fails adds nothing."""
        by_mod: dict[str, list[dict]] = {}
        for it in items:
            if "." in it["type"]:
                by_mod.setdefault(it["type"].split(".", 1)[0], []).append(it)
        out: dict[str, dict] = {}
        for mid, its in by_mod.items():
            mod = self.board_owner(its[0]["type"])
            if mod is None:
                continue
            try:
                got = await mod.board_data(its)
            except Exception:
                log.exception("module %s: board_data failed", mid)
                continue
            ids = {i["id"] for i in its}
            out.update({k: v for k, v in (got or {}).items() if k in ids and isinstance(v, dict)})
        return out

    def pills(self) -> list[dict]:
        out: list[dict] = []
        for mod in self.active():
            try:
                out.extend(mod.pills())
            except Exception:
                log.exception("module %s pills failed", mod.manifest.id)
        return out

    def stream(self, name: str, request: Any, params: dict) -> Any:
        for mod in self.active():
            if name in mod.streams:
                resp = mod.stream(name, request, params)
                if resp is not None:
                    return resp
        return None

    def streams(self) -> set[str]:
        """The stream names the modules that are on serve."""
        return {n for mod in self.active() for n in mod.streams}

    def listeners(self, cfg: dict | None = None) -> list[tuple[Module, Listener, int]]:
        """(module, listener, port) for each listener of the modules that are on, with the port from its settings."""
        from .. import config

        cfg = cfg if cfg is not None else config.get()
        out = []
        for mod in self.active():
            settings = cfg.get(mod.manifest.settings_key) or {}
            for lst in mod.listeners:
                out.append((mod, lst, listener_port(settings, lst)))
        return out

    def listener_prefixes(self) -> tuple[str, ...]:
        """Every path prefix a module serves only on a listener of its own, whether the module is on or not."""
        return tuple(p for mod in self.all() for lst in mod.listeners for p in lst.prefixes)

    def panel_prefixes(self) -> tuple[str, ...]:
        """Every path prefix a module serves only on the HTTPS operator port, whether the module is on or not."""
        return tuple(p for mod in self.all() for p in mod.panel_prefixes)

    def health(self, mod: Module) -> Health:
        if not self.enabled(mod.manifest.id):
            return Health("idle", "Off")
        try:
            health = mod.health()
        except Exception as exc:
            log.exception("module %s health failed", mod.manifest.id)
            return Health("error", f"Health check failed: {type(exc).__name__}")
        # A strict traffic class whose chosen network port is missing or down (app/netroute.py): say so for every
        # module, so none has to write the sentence itself.
        if mod.manifest.traffic:
            from .. import netroute

            problem = netroute.health_problem(mod.manifest.id)
            if problem:
                state = "error" if health.state == "error" else "warn"
                return Health(state, " ".join(x for x in (problem, health.detail) if x))
        return health

    # ------------------------------------------------------------ config
    def clean_config(self, cfg: dict) -> None:
        """Normalise each module's settings and its switch, in place (config._normalise)."""
        stored = cfg.get("modules") if isinstance(cfg.get("modules"), dict) else {}
        self._drop_retired(cfg)
        for mod in self.all():
            if mod.manifest.renamed_from:
                stored = self._move_renamed(cfg, stored, mod.manifest)
        switches: dict[str, dict] = {}
        for mod in self.all():
            m = mod.manifest
            cfg[m.settings_key] = mod.clean(cfg.get(m.settings_key))
            entry = stored.get(m.id)
            if isinstance(entry, dict) and isinstance(entry.get("enabled"), bool):
                switches[m.id] = {"enabled": entry["enabled"]}
            else:
                # No switch saved: on if the operator had already set the integration up, so an upgrade
                # changes nothing for a church that uses it.
                switches[m.id] = {"enabled": bool(mod.configured(cfg[m.settings_key]))}
        cfg["modules"] = switches

    @staticmethod
    def _drop_retired(cfg: dict) -> None:
        """Settings, switch and network routes saved by a module that no longer exists (RETIRED_MODULES), removed."""
        for old in RETIRED_MODULES:
            cfg.pop(old, None)
            if isinstance(cfg.get("modules"), dict):
                cfg["modules"].pop(old, None)
            net = cfg.get("network")
            if isinstance(net, dict):
                for part in ("routes", "policy"):
                    table = net.get(part)
                    if isinstance(table, dict):
                        for cid in [c for c in table if c == old or c.startswith(old + ".")]:
                            del table[cid]

    @staticmethod
    def _move_renamed(cfg: dict, stored: dict, m: Manifest) -> dict:
        """A renamed module's settings, switch and network routes saved under its old id, moved to the new one. Nothing
        is overwritten: what is already under the new id wins. Returns the module switches as they now are."""
        old, new = m.renamed_from, m.id
        if old != m.settings_key and old in cfg and m.settings_key not in cfg:
            cfg[m.settings_key] = cfg.pop(old)
        stored = dict(stored)
        if old in stored:
            moved = stored.pop(old)
            stored.setdefault(new, moved)
        net = cfg.get("network")
        if isinstance(net, dict):
            for part in ("routes", "policy"):
                table = net.get(part)
                if isinstance(table, dict):
                    for cid in [c for c in table if c == old or c.startswith(old + ".")]:
                        table.setdefault(new + cid[len(old):], table[cid])
                        del table[cid]
        return stored

    def secret_paths(self) -> list[tuple[str, str]]:
        """(settings key, field) of every module secret config.json keeps sealed, whether the module is on or off."""
        return [(mod.manifest.settings_key, field) for mod in self.all() for field in mod.secret_settings]

    def redact_config(self, cfg: dict) -> None:
        for mod in self.all():
            key = mod.manifest.settings_key
            if key in cfg:
                cfg[key] = mod.public(cfg[key])

    # ------------------------------------------------------------ admin page
    def describe(self, mod: Module) -> dict:
        m = mod.manifest
        health = self.health(mod)
        return {
            "id": m.id, "name": m.name, "version": m.version, "summary": m.summary, "guide": m.guide,
            "uses": [{"id": u, "name": (self.get(u).manifest.name if self.get(u) else u),
                      "enabled": self.enabled(u)} for u in m.uses],
            "enabled": self.enabled(m.id),
            "health": {"state": health.state, "detail": health.detail},
            "pages": [{"id": p.id, "title": p.title} for p in mod.admin_pages],
            "has_test": type(mod).test is not Module.test,
        }

    @staticmethod
    def _page_permission(mod: Module, page: AdminPage) -> str:
        return page.permission or f"{mod.manifest.id}.view"

    def admin_nav(self, user: User | None = None) -> list[dict]:
        """The sidebar entries of the modules that are on, for admin.html: one per module, in NAV_SECTIONS order,
        each with the admin pages under it. With a `user`, only the pages that user may see, and no entry for a
        module with none; with None, all of them (for code that has no user)."""
        entries: list[dict] = []
        for mod in self.active():
            m = mod.manifest
            pages = [{
                "id": p.id, "title": p.title, "icon": p.icon,
                "template": f"{m.id}/{p.template}",
                "module": m.name, "version": m.version, "guide": m.guide,
            } for p in mod.admin_pages if not p.tab_of and (user is None or user.can(self._page_permission(mod, p)))]
            if not pages:
                continue
            health = self.health(mod)
            section = m.section if m.section in NAV_SECTIONS else "Modules"
            entries.append({"id": m.id, "name": m.name, "section": section, "icon": pages[0]["icon"],
                            "health": health.state, "detail": health.detail, "pages": pages,
                            "tabbed": mod.admin_tabbed})
        entries.sort(key=lambda e: NAV_SECTIONS.index(e["section"]))
        return entries

    def admin_tabs(self, user: User | None = None) -> dict[str, list[dict]]:
        """{core page id: [tab, ...]}: the admin pages of the modules that are on that are tabs of a core page, in
        module order, each only where `user` may see it."""
        out: dict[str, list[dict]] = {}
        for mod in self.active():
            m = mod.manifest
            for p in mod.admin_pages:
                if p.tab_of in TAB_PAGES and (user is None or user.can(self._page_permission(mod, p))):
                    out.setdefault(p.tab_of, []).append({
                        "id": p.id, "title": p.title, "template": f"{m.id}/{p.template}", "module": m.name,
                        "module_id": m.id, "version": m.version, "guide": m.guide})
        return out

    def health_states(self, max_age: float = 5.0) -> dict[str, str]:
        """{module id: health state} for the modules that are on, for the sidebar's dots. Kept for `max_age`
        seconds, because the status it rides on is pushed on every change. The detail stays out: the status goes
        to every admin page, and only someone who may see a module's page reads why."""
        now = time.monotonic()
        cached = self._health_cache
        if cached is not None and now - cached[0] < max_age:
            return cached[1]
        out = {mod.manifest.id: self.health(mod).state for mod in self.active()}
        self._health_cache = (now, out)
        return out

    def user_nav(self, user: User) -> list[dict]:
        """The home-page links of the modules that are on that `user` may use."""
        return [{"title": p.title, "note": p.note, "path": p.path}
                for mod in self.active() for p in mod.user_pages if user.can(p.permission)]

    def crew_nav(self, user: User) -> list[dict]:
        """The crew-page tabs of the modules that are on that `user` may use."""
        return [{"id": t.id, "title": t.title, "template": f"{mod.manifest.id}/{t.template}"}
                for mod in self.active() for t in mod.crew_tabs if user.can(t.permission)]

    def crew_scripts(self, user: User) -> list[str]:
        """The scripts of the modules that are on and have a tab `user` may use."""
        return [f"{mod.manifest.id}/{s}" for mod in self.active()
                if any(user.can(t.permission) for t in mod.crew_tabs) for s in mod.crew_scripts]

    def crew_styles(self, user: User) -> list[str]:
        """The stylesheets of the modules that are on and have a tab `user` may use."""
        return [f"{mod.manifest.id}/{s}" for mod in self.active()
                if any(user.can(t.permission) for t in mod.crew_tabs) for s in mod.crew_styles]

    def microphone_pages(self) -> tuple[str, ...]:
        """The page paths that may use the microphone: those named by the modules that are on."""
        return tuple(p for mod in self.active() for p in mod.microphone_pages)

    def geolocation_pages(self) -> tuple[str, ...]:
        """The page paths that may use the location: those named by the modules that are on."""
        return tuple(p for mod in self.active() for p in mod.geolocation_pages)

    def _admin_files(self, attr: str, user: User | None) -> list[str]:
        out: list[str] = []
        for mod in self.active():
            if user is not None:
                pages = [self._page_permission(mod, p) for p in mod.admin_pages] or [f"{mod.manifest.id}.view"]
                if not any(user.can(perm) for perm in pages):
                    continue
            out += [f"{mod.manifest.id}/{s}" for s in getattr(mod, attr)]
        return out

    def admin_scripts(self, user: User | None = None) -> list[str]:
        """The scripts of the modules that are on. With a `user`, only those of a module with at least one page
        they may see (or, for a module with no pages, when they hold its `.view`)."""
        return self._admin_files("admin_scripts", user)

    def admin_styles(self, user: User | None = None) -> list[str]:
        """The stylesheets of the modules that are on, chosen as admin_scripts are (#463)."""
        return self._admin_files("admin_styles", user)


def listener_port(settings: dict, lst: Listener) -> int:
    try:
        port = int(settings.get(lst.port_setting) or lst.default_port)
    except (TypeError, ValueError):
        return lst.default_port
    return port if 1 <= port <= 65535 else lst.default_port


def under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def validate(mod: Any, package: str) -> list[str]:
    """What is wrong with a module definition (empty when nothing is). Used by discovery and the tests."""
    problems: list[str] = []
    if not isinstance(mod, Module):
        return [f"{package}: MODULE must be an instance of app.modules.Module"]
    m = getattr(mod, "manifest", None)
    if not isinstance(m, Manifest):
        return [f"{package}: MODULE.manifest must be a Manifest"]
    if m.id != package:
        problems.append(f"{package}: manifest id {m.id!r} must equal the package name")
    if not _ID_RE.match(m.id):
        problems.append(f"{package}: manifest id must be lower_snake_case")
    if not _VERSION_RE.match(m.version):
        problems.append(f"{package}: version {m.version!r} must be MAJOR.MINOR.PATCH")
    if not m.name.strip() or not m.summary.strip():
        problems.append(f"{package}: name and summary are required")
    if not m.settings_key or m.settings_key == "modules":
        problems.append(f"{package}: settings_key must name the module's own config key")
    if m.renamed_from and (not _ID_RE.match(m.renamed_from) or m.renamed_from == m.id):
        problems.append(f"{package}: renamed_from must be a different lower_snake_case id")
    for page in mod.admin_pages:
        if not (HERE / package / "templates" / page.template).is_file():
            problems.append(f"{package}: admin page template {page.template} is missing")
    for script in mod.admin_scripts:
        if not (HERE / package / "static" / script).is_file():
            problems.append(f"{package}: admin script {script} is missing")
    for style in (*mod.admin_styles, *mod.crew_styles):
        if not style.endswith(".css") or not (HERE / package / "static" / style).is_file():
            problems.append(f"{package}: stylesheet {style} is missing")
    for tab in mod.crew_tabs:
        if not (HERE / package / "templates" / tab.template).is_file():
            problems.append(f"{package}: crew tab template {tab.template} is missing")
    for script in mod.crew_scripts:
        if not (HERE / package / "static" / script).is_file():
            problems.append(f"{package}: crew script {script} is missing")
    declared = set()
    for perm in mod.permissions:
        if not isinstance(perm, Permission):
            problems.append(f"{package}: permissions must be users.Permission values")
        elif not perm.id.startswith(m.id + "."):
            problems.append(f"{package}: permission {perm.id!r} must start with {m.id + '.'!r}")
        else:
            declared.add(perm.id)
    known = declared | {p.id for p in users_mod.catalogue()}
    for page in mod.admin_pages:
        want = page.permission or f"{m.id}.view"
        if want not in known and (page.permission or declared):
            problems.append(f"{package}: admin page {page.id} needs permission {want!r}, which is not declared")
    for page in mod.user_pages:
        if page.permission not in declared:
            problems.append(f"{package}: user page {page.title!r} needs one of the module's own permissions")
        if not any(page.path == pre or page.path.startswith(pre.rstrip("/") + "/") for pre in mod.route_prefixes):
            problems.append(f"{package}: user page {page.title!r} must be under the module's route_prefixes")
    for pre in mod.panel_prefixes:
        if not under(pre, mod.route_prefixes):
            problems.append(f"{package}: panel prefix {pre!r} must be under the module's route_prefixes")
    for tab in mod.crew_tabs:
        if tab.permission not in declared:
            problems.append(f"{package}: crew tab {tab.title!r} needs one of the module's own permissions")
    names = set()
    for lst in mod.listeners:
        if not isinstance(lst, Listener):
            problems.append(f"{package}: listeners must be Listener values")
            continue
        if lst.name in names:
            problems.append(f"{package}: listener {lst.name!r} is declared twice")
        names.add(lst.name)
        if not lst.prefixes or not all(under(p, mod.route_prefixes) for p in lst.prefixes):
            problems.append(f"{package}: listener {lst.name!r} prefixes must be under the module's route_prefixes")
        if not 1024 <= lst.default_port <= 65535:
            problems.append(f"{package}: listener {lst.name!r} default port must be 1024-65535")
    from .. import netroute

    own_traffic = {t.id for t in m.traffic if isinstance(t, Traffic)}
    for lst in mod.listeners:
        if isinstance(lst, Listener) and lst.traffic not in own_traffic | {t.id for t in netroute.CORE_TRAFFIC}:
            problems.append(f"{package}: listener {lst.name!r} traffic {lst.traffic!r} must be a core class or its own")
    seen_traffic = set()
    for t in m.traffic:
        if not isinstance(t, Traffic):
            problems.append(f"{package}: traffic must be Traffic values")
            continue
        if not netroute.owns(m.id, t.id) or not netroute._CLASS_RE.match(t.id):
            problems.append(f"{package}: traffic {t.id!r} must be {m.id!r} or start with {m.id + '.'!r}")
        if t.id in seen_traffic:
            problems.append(f"{package}: traffic {t.id!r} is declared twice")
        if not t.label.strip():
            problems.append(f"{package}: traffic {t.id!r} needs a label")
        seen_traffic.add(t.id)
    return problems


def package_names() -> list[str]:
    return sorted(info.name for info in pkgutil.iter_modules([str(HERE)]) if info.ispkg)


def discover() -> dict[str, Module]:
    found: dict[str, Module] = {}
    for name in package_names():
        pkg = importlib.import_module(f"{__name__}.{name}")
        mod = getattr(pkg, "MODULE", None)
        problems = validate(mod, name)
        if problems:
            raise RuntimeError("; ".join(problems))
        users_mod.register(mod.permissions)
        found[name] = mod
    return found


modules = Registry()
