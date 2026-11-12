"""Relay modules (#157): every integration is a module that is switched on and off on its own.

Relay's core is captions, translation, panels, alerts, schedules and recordings. Anything that talks to
another product (ProPresenter, Planning Center, mixing consoles, lighting, streams) is a module: a package
under app/modules/<id>/ whose `__init__.py` defines `MODULE`, an instance of `Module`. Modules ship inside
the signed Docker image; nothing is downloaded at run time. The operator switches each one on or off on
the admin page's Modules page, and a module that is off carries none of its code paths:

* its background work is never started (`start`/`stop`),
* every route it adds answers 404 (the registry mounts its routers behind `require_enabled`),
* its admin pages, nav links and scripts are not rendered,
* it adds nothing to the status, the panel feeds or the caption streams.

The direction of imports is one way: a module may import core (config, hub, engine, panels, alerts, ...);
core never imports a module. Core reaches modules only through this registry and the hooks below. A module
that works with another one (the ProPresenter link to Planning Center) names it in `Manifest.uses` and must
cope with it being off. tests/test_modules.py enforces all of this; docs/modules.md is the guide.

The registry itself is core and imports each module package to read its manifest. Importing a module runs
nothing: work starts only in `start`, which is called only while the module is on.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

log = logging.getLogger("relay.modules")

HERE = Path(__file__).resolve().parent
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
HEALTH_STATES = ("ok", "warn", "error", "idle")


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
    # Other modules this one does more with when they are on too. Only these may be imported from it.
    uses: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdminPage:
    """A page the module adds to the admin panel, rendered only while the module is on."""
    id: str            # the #hash and data-page name; unique across Relay
    title: str         # the nav label
    template: str      # a template in the module's templates/ directory
    group: str         # the nav group it is listed under: "Live", "Output", "Automation" or "Settings"
    icon: str = ""     # inner SVG for the 16x16 nav icon


@dataclass(frozen=True)
class Health:
    state: str         # one of HEALTH_STATES
    detail: str = ""


class Module:
    """The interface every module implements. Subclasses set `manifest` and override what they need."""

    manifest: Manifest
    # Settings stored under manifest.settings_key when nothing is saved yet.
    defaults: dict[str, Any] = {}
    # Admin pages, and the scripts in the module's static/ directory the admin page loads for it.
    admin_pages: tuple[AdminPage, ...] = ()
    admin_scripts: tuple[str, ...] = ()
    # Path prefixes of every route the module adds. Core templates and scripts must not name them
    # (tests/test_modules.py), so the only way into a module is through pages it renders itself.
    route_prefixes: tuple[str, ...] = ()

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
    async def start(self) -> None:
        """Start background work. Called at startup while on, and when switched on."""

    async def stop(self) -> None:
        """Stop everything `start` began, and clear any hook it set. Called when switched off and at
        shutdown."""

    def health(self) -> Health:
        return Health("idle", "")

    async def test(self) -> dict:
        """Test the connection with the saved settings: {"ok": bool, "detail": str}."""
        return {"ok": False, "detail": "This module has no connection test."}

    # ------------------------------------------------------------ hooks into core
    def status(self) -> dict:
        """Keys merged into the engine status the admin page and panels read."""
        return {}

    def stream(self, name: str, request: Any, params: dict) -> Any:
        """A /stream response for a stream this module owns, or None if `name` is not one of its own."""
        return None


# ------------------------------------------------------------------ what core gives modules
# A module cannot import app.main (main imports this registry), so the few things its routes need from the
# web layer are here.
HEARTBEAT_S = 15.0
# main sets this to its own admin check (session cookie or x-admin-token).
admin_check: Callable[[Request], bool] | None = None


def require_admin(request: Request) -> bool:
    """FastAPI dependency for a module's admin routes: the same login as the rest of the admin panel."""
    if admin_check is None:
        raise HTTPException(status_code=401, detail="Admin token required")
    return admin_check(request)


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
        for mod in self.all():
            await self._stop(mod)

    async def _start(self, mod: Module) -> None:
        mid = mod.manifest.id
        if mid in self._running:
            return
        try:
            await mod.start()
        except Exception:
            log.exception("module %s failed to start", mid)
            return
        self._running.add(mid)
        log.info("Module on: %s %s", mod.manifest.name, mod.manifest.version)

    async def _stop(self, mod: Module) -> None:
        mid = mod.manifest.id
        if mid not in self._running:
            return
        self._running.discard(mid)
        try:
            await mod.stop()
        except Exception:
            log.exception("module %s failed to stop", mid)
        log.info("Module off: %s", mod.manifest.name)

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

    def stream(self, name: str, request: Any, params: dict) -> Any:
        for mod in self.active():
            resp = mod.stream(name, request, params)
            if resp is not None:
                return resp
        return None

    def health(self, mod: Module) -> Health:
        if not self.enabled(mod.manifest.id):
            return Health("idle", "Off")
        try:
            return mod.health()
        except Exception as exc:
            log.exception("module %s health failed", mod.manifest.id)
            return Health("error", f"Health check failed: {type(exc).__name__}")

    # ------------------------------------------------------------ config
    def clean_config(self, cfg: dict) -> None:
        """Normalise each module's settings and its switch, in place (config._normalise)."""
        stored = cfg.get("modules") if isinstance(cfg.get("modules"), dict) else {}
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

    def admin_nav(self) -> dict[str, list[dict]]:
        """The admin pages of the modules that are on, by nav group, for admin.html."""
        groups: dict[str, list[dict]] = {}
        for mod in self.active():
            for p in mod.admin_pages:
                groups.setdefault(p.group, []).append({
                    "id": p.id, "title": p.title, "icon": p.icon,
                    "template": f"{mod.manifest.id}/{p.template}",
                    "module": mod.manifest.name, "version": mod.manifest.version, "guide": mod.manifest.guide,
                })
        return groups

    def admin_scripts(self) -> list[str]:
        return [f"{mod.manifest.id}/{s}" for mod in self.active() for s in mod.admin_scripts]


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
    for page in mod.admin_pages:
        if not (HERE / package / "templates" / page.template).is_file():
            problems.append(f"{package}: admin page template {page.template} is missing")
    for script in mod.admin_scripts:
        if not (HERE / package / "static" / script).is_file():
            problems.append(f"{package}: admin script {script} is missing")
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
        found[name] = mod
    return found


modules = Registry()
