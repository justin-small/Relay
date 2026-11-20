"""Relay -- FastAPI host.

Viewer pages are read-only and unauthenticated (venue LAN). The admin panel is
behind a token. The OpenAI key never leaves this process.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from . import alerts as alertsmod
from . import backup as backupmod
from . import announce as announcemod
from . import aimodels, alerthistory, audiobus, audit, boards, cables, channels, config, db, devices, exporting, importdocker, languages, metering, mixreport, netinfo, network_map, netroute, overlays, panelfw, panels, ports, prefs, redact, routing, schedules, screens, serving, setupcode, streamstatus, tally, tls, transcribe, users
from .alerts import alerts
from . import engine as engine_mod
from .engine import TRANSCRIPTION_STREAM, TRANSLATION_STREAM, demo_mode, engine
from .hub import hub
from . import modules as modules_pkg
from . import sidecar
from .modules import HERE as MODULES_DIR, Module, modules as module_registry, under as _module_under
from .panels import registry as panel_registry
from .recorder import recorder
from . import scheduler as scheduler_mod
from .scheduler import plan_source as scheduler_plan_source, scheduler
from .users import User

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s  %(message)s"
)
log = logging.getLogger("relay")

BASE = config.ROOT / "app"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Replaces the deprecated @app.on_event hooks. _startup/_shutdown are
    # defined below and resolve at call time.
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app = FastAPI(
    title="Relay", docs_url=None, redoc_url=None, lifespan=lifespan
)
class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must check with Relay before reuse. Without a Cache-Control header,
    browsers guess a freshness lifetime from Last-Modified and keep serving an old stylesheet or script
    after an update (a new page then rendered unstyled). With no-cache an unchanged file is still a
    cheap 304 from its ETag."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


# Each module's own static files, at /static/modules/<id>/ (mounted before /static, which would match first).
# Served whether the module is on or off: they are inert files, and nothing links to them while it is off.
for _mod in module_registry.all():
    _mid = _mod.manifest.id
    if (MODULES_DIR / _mid / "static").is_dir():
        app.mount(f"/static/modules/{_mid}", RevalidatedStaticFiles(directory=MODULES_DIR / _mid / "static"),
                  name=f"static-{_mid}")
app.mount("/static", RevalidatedStaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
# A module's templates are named "<module id>/<file>", from app/modules/<id>/templates/.
templates.env.loader = ChoiceLoader([
    FileSystemLoader(str(BASE / "templates")),
    PrefixLoader({m.manifest.id: FileSystemLoader(str(MODULES_DIR / m.manifest.id / "templates"))
                  for m in module_registry.all()}),
])


def _static_file(path: str):
    """The file behind a static path: "modules/<id>/<file>" is in that module's static/ directory."""
    if path.startswith("modules/"):
        _, mid, rest = path.split("/", 2)
        return MODULES_DIR / mid / "static" / rest
    return BASE / "static" / path


def _static_url(path: str) -> str:
    """A /static URL that changes whenever the file does, so a browser holding a copy cached before
    no-cache was sent (or before an update) fetches the new one instead of reusing the old."""
    try:
        version = format(_static_file(path).stat().st_mtime_ns, "x")
    except (OSError, ValueError):
        return "/static/" + path
    return f"/static/{path}?v={version}"


templates.env.globals["static"] = _static_url

ADMIN_COOKIE = "relay_admin"
DEVICE_COOKIE = "relay_device"
DEVICE_COOKIE_MAX_AGE = 2 * 365 * 24 * 3600
BACKUP_FIRST_DELAY_S = 60.0      # after start-up, so the dump does not compete with the boot
BACKUP_EVERY_S = 3600.0
LOGIN_FAIL_DELAY_S = 0.75
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT_S = 600.0              # 10 minutes (#278)
HEARTBEAT_S = 15.0
BLOCKLIST_POLL_S = 2.0

# The operator panel lives on its own port. Everything under these prefixes is
# refused on the viewer port, so a link handed to a room cannot reach the panel
# -- not even the login form.
ADMIN_PREFIXES = ("/admin", "/api/admin", "/home")


def _request_port(request: Request) -> int | None:
    server = request.scope.get("server") or ()
    return server[1] if len(server) > 1 else None


def _is_admin_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in ADMIN_PREFIXES)


def _on_admin_port(request: Request) -> bool:
    """False only for a request that arrived on the viewer port.

    The viewer port is the one Relay really bound (app/serving.py: 80, or 8080 when 80 was taken), or the
    configured one when nothing has been published (a test client, a script importing the app).
    """
    return _request_port(request) != serving.viewer_port(config.get()["port"])


def _on_panel_socket(request: Request) -> bool:
    """True when this request arrived on the HTTPS panel socket."""
    return _request_port(request) == serving.admin_port(config.get()["admin_port"])


# What Caddy used to add to every panel response. HSTS is only honoured over HTTPS, so it is only sent there.
PANEL_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # SAMEORIGIN, not DENY: the overlay builder previews the real /overlay page in a frame on the panel itself.
    # Other sites still cannot frame the panel.
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}
HSTS = f"max-age={tls.HSTS_MAX_AGE}"


@app.middleware("http")
async def _panel_headers(request: Request, call_next):
    response = await call_next(request)
    if _on_panel_socket(request):
        for k, v in PANEL_HEADERS.items():
            response.headers[k] = v
        if _is_https(request):
            response.headers["Strict-Transport-Security"] = HSTS
    return response


# Web Serial is denied everywhere but the board flasher, which writes a panel's flash through it. An
# exact path and not a prefix, so nothing added under it later gets the serial port too.
FLASH_PATH = "/admin/flash"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()"
FLASH_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(self)"


# A page a switched-on module names in `microphone_pages` (the crew page, for the intercom, #240) may use the
# microphone itself and nothing embedded in it. Every other page keeps microphone=().
MIC_PERMISSIONS_POLICY = "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), serial=()"


def permissions_policy_for(path: str) -> str:
    if path == FLASH_PATH:
        return FLASH_PERMISSIONS_POLICY
    policy = MIC_PERMISSIONS_POLICY if path in module_registry.microphone_pages() else PERMISSIONS_POLICY
    # Likewise a page a switched-on module names in `geolocation_pages` (the crew page, for check-in, #231).
    return policy.replace("geolocation=()", "geolocation=(self)") if path in module_registry.geolocation_pages() else policy


@app.middleware("http")
async def _permissions_policy(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Permissions-Policy", permissions_policy_for(request.url.path))
    return response


@app.middleware("http")
async def _split_admin_port(request: Request, call_next):
    path = request.url.path
    # A module's own port (a Listener, #205) answers only that module's routes under its prefixes; those routes
    # answer on no other port. Until ports are published (tests, scripts) nothing is gated by port.
    own = serving.listener_prefixes(_request_port(request))
    if own is not None:
        if not _module_under(path, own):
            return JSONResponse({"error": "Not found"}, status_code=404)
        return await call_next(request)
    if serving.published() and _module_under(path, module_registry.listener_prefixes()):
        return JSONResponse({"error": "Not found"}, status_code=404)
    if (_is_admin_path(path) or _module_under(path, module_registry.panel_prefixes())) \
            and not _on_admin_port(request):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return await call_next(request)


# ----------------------------------------------------- the audit log (#172)
_AUDIT_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# What each core admin change is called in the audit log, by route function: (action, summary). Its path
# parameters become the entry's target. Routes with more to say (users, tokens, devices, settings, modules)
# also call audit.note, which takes the place of this. A route that is in neither (a module's) is entered as
# "<METHOD> <path>". tests/test_audit.py fails a core admin route that changes something and is not listed.
AUDIT_ROUTES = {
    "admin_module_switch": ("module.switch", "Switched a module on or off"),
    "admin_module_test": ("module.test", "Ran a module's test"),
    "admin_network_route": ("network.route", "Changed which network port some traffic uses"),
    "admin_network_test": ("network.test", "Tested a traffic route"),
    "admin_network_cable_add": ("network.cable_add", "Recorded a cable between two ports"),
    "admin_network_cable_note": ("network.cable_note", "Changed a note on a cable"),
    "admin_network_cable_remove": ("network.cable_remove", "Removed a cable between two ports"),
    "admin_network_channel_rename": ("network.channel_rename", "Renamed a channel"),
    "admin_network_port_rename": ("network.port_rename", "Renamed a port in Relay only"),
    "admin_network_channel_note": ("network.channel_note", "Changed a note on a channel"),
    "admin_network_channel_merge": ("network.channel_merge", "Joined two channels into one"),
    "admin_network_channel_split": ("network.channel_split", "Split a channel apart"),
    "admin_network_channel_decline": ("network.channel_decline", "Said two channels are not the same"),
    "admin_network_channel_forget": ("network.channel_forget", "Forgot a channel"),
    "admin_routes_set": ("routes.set", "Routed a source to a destination"),
    "admin_routes_clear": ("routes.clear", "Took a destination off a source"),
    "admin_routes_salvo_save": ("routes.salvo_save", "Saved a route preset"),
    "admin_routes_salvo_take": ("routes.salvo_take", "Took a route preset"),
    "admin_routes_salvo_delete": ("routes.salvo_delete", "Deleted a route preset"),
    "admin_network_forget": ("network.forget", "Forgot a network port"),
    "admin_network_labels": ("network.labels", "Changed device names or notes on the network map"),
    "admin_network_places": ("network.places", "Changed where the network gear is"),
    "admin_config": ("settings.change", "Changed settings"),
    "admin_audiobus_save": ("audio.bus", "Changed the audio bus"),
    "admin_metering_save": ("audio.metering", "Changed the sound meter"),
    "admin_mixreport_save": ("audio.mixreport", "Changed the stream-mix report"),
    "admin_metering_calibrate": ("audio.metering", "Calibrated the sound meter"),
    "admin_target": ("session.target", "Changed a target language"),
    "admin_start": ("session.start", "Started captions"),
    "admin_stop": ("session.stop", "Stopped captions"),
    "admin_alerts_save": ("alerts.change", "Changed the alert setup"),
    "admin_alerts_test": ("alerts.test", "Sent a test to a panel or screen"),
    "admin_panel_firmware_upload": ("panels.firmware_upload", "Uploaded panel firmware"),
    "admin_panel_firmware_delete": ("panels.firmware_delete", "Deleted the uploaded panel firmware"),
    "admin_panel_firmware_update": ("panels.firmware_update", "Sent a firmware update to panels"),
    "admin_panel_flash_upload": ("panels.flash_upload", "Uploaded a board flash image"),
    "admin_panel_flash_delete": ("panels.flash_delete", "Deleted the board flash image"),
    "admin_panel_update": ("panels.change", "Changed a panel"),
    "admin_panel_reboot": ("panels.reboot", "Rebooted a panel"),
    "admin_panel_forget": ("panels.forget", "Forgot a panel"),
    "admin_schedule_create": ("schedules.create", "Added a schedule"),
    "admin_schedule_update": ("schedules.change", "Changed a schedule"),
    "admin_schedule_delete": ("schedules.delete", "Deleted a schedule"),
    "admin_screen_update": ("screens.change", "Changed a screen"),
    "admin_screen_delete": ("screens.delete", "Deleted a screen"),
    "admin_screen_hold": ("screens.hold", "Put something on a screen by hand"),
    "admin_screen_hold_clear": ("screens.hold", "Returned a screen to its schedule"),
    "admin_screen_flash": ("screens.flash", "Flashed a screen"),
    "admin_place_create": ("screens.place_create", "Added a place"),
    "admin_place_update": ("screens.place_change", "Renamed a place"),
    "admin_place_delete": ("screens.place_delete", "Deleted a place"),
    "admin_place_flash": ("screens.flash", "Flashed every screen in a place"),
    "admin_overlay_create": ("overlays.create", "Added an overlay"),
    "admin_overlay_update": ("overlays.change", "Changed an overlay"),
    "admin_overlay_delete": ("overlays.delete", "Deleted an overlay"),
    "admin_board_create": ("boards.create", "Added a board"),
    "admin_board_update": ("boards.change", "Changed a board"),
    "admin_board_duplicate": ("boards.create", "Duplicated a board"),
    "admin_board_import": ("boards.create", "Imported a board"),
    "admin_board_delete": ("boards.delete", "Deleted a board"),
    "admin_board_screen_name": ("boards.change", "Named a screen showing a board"),
    "admin_board_reload": ("boards.reload", "Reloaded every screen showing a board"),
    "admin_board_page": ("boards.page", "Chose the page a board shows"),
    "admin_recording_delete": ("recordings.delete", "Deleted a recording"),
    "admin_me_password": ("account.password", "Changed their own password"),
    "admin_me_pin": ("account.pin_set", "Set their PIN"),
    "admin_me_pin_clear": ("account.pin_clear", "Removed their PIN"),
    "admin_me_token_create": ("token.create", "Made an API token"),
    "admin_me_token_revoke": ("token.revoke", "Revoked one of their API tokens"),
    "admin_token_revoke": ("token.revoke", "Revoked an API token"),
    "admin_user_create": ("user.create", "Added a user"),
    "admin_user_update": ("user.update", "Changed a user"),
    "admin_user_password": ("user.password_reset", "Reset a user's password"),
    "admin_user_signout": ("user.signout", "Signed a user out everywhere"),
    "admin_user_delete": ("user.delete", "Deleted a user"),
    "admin_device_trust": ("device.trust", "Trusted a browser for PIN sign-in"),
    "admin_device_revoke": ("device.revoke", "Forgot a trusted device"),
    "admin_phone_rename": ("phone.rename", "Renamed a phone"),
    "admin_phone_revoke": ("phone.revoke", "Forgot a phone: signed it out of sign-in, intercom, check-in and push"),
    "admin_phone_merge": ("phone.merge", "Joined two phones that are the same one"),
    "admin_me_phone_rename": ("phone.rename", "Renamed one of their phones"),
    "admin_me_phone_revoke": ("phone.revoke", "Forgot one of their phones: signed it out of sign-in, intercom, check-in and push"),
}

# Core admin routes that change something but are not entered in the audit log are listed in AUDIT_SKIP_ENDPOINTS,
# after the routes themselves (#287).


def _audit_default(request: Request) -> dict:
    """The entry for an admin change whose route noted nothing itself."""
    route = request.scope.get("route")
    params = {k: str(v) for k, v in (request.scope.get("path_params") or {}).items()}
    known = AUDIT_ROUTES.get(getattr(route, "name", ""))
    if known:
        return {"action": known[0], "summary": known[1], "target": ", ".join(params.values()), "detail": None}
    path = request.url.path
    return {"action": "api.change", "summary": f"{request.method} {path}", "target": "",
            "detail": {"method": request.method, "path": path}}


def _audit_write(notes: list[dict], user: User, ip: str) -> None:
    for n in notes:
        audit.record(n["action"], n["summary"], actor=user, target=n.get("target") or "",
                     detail=n.get("detail"), ip=ip)


@app.middleware("http")
async def _audit_changes(request: Request, call_next):
    """Every change made through /api/admin goes in the audit log, with who made it, once it succeeded."""
    response = await call_next(request)
    if (request.method in _AUDIT_METHODS and request.url.path.startswith("/api/admin/")
            and response.status_code < 400
            and getattr(request.scope.get("route"), "endpoint", None) not in AUDIT_SKIP_ENDPOINTS):
        user = getattr(request.state, "user", None)
        if isinstance(user, User):
            notes = audit.pending(request) or [_audit_default(request)]
            await asyncio.to_thread(_audit_write, notes, user, _client_ip(request))
    return response


def _on_lan_change(change: netinfo.Change) -> None:
    """The network changed. When the LAN addresses did: re-mint the panel certificate if its names no longer
    fit, and tell the desktop shell (it pins the fingerprint and builds the viewer link) of both. Every change
    goes to the traffic routes, which tell the modules whose port moved (app/netroute.py), moves any listener whose
    routed port changed address (app/serving.py), and checks the certificate still names every HTTPS address."""
    netroute.on_network_change(change.ports)
    if serving.groups():
        task = asyncio.get_running_loop().create_task(serving.rebind())
        _rebinds.add(task)
        task.add_done_callback(_rebinds.discard)
    if tls.panel.refresh() and tls.panel.info:
        sidecar.emit("fingerprint", fingerprint=tls.panel.info.fingerprint)
    if change.ips_changed:
        sidecar.emit("network", ips=list(change.ips))


_rebinds: set = set()   # running rebind tasks, held so they are not collected mid-flight


async def _startup() -> None:
    config.ensure_file()
    config.load()
    # The user database. Down is not fatal: viewers and panels never need it, and the admin page says so.
    ready = await asyncio.to_thread(_ready_quietly)
    if not ready:
        log.warning("The user database is not available: the operator panel is closed until it is.")
    app.state.backup_task = asyncio.create_task(_backup_loop())
    # Record each module's switch once (on if its integration was already set up), then start those that are on.
    module_registry.settle()
    cfg = config.get()
    hub.history_lines = cfg["history_lines"]
    path = config.ensure_blocklist_file()
    hub.set_blocklist(redact.read_blocklist_file(path))
    log.info("Blocked words: %d term(s) from %s", len(hub.blocklist), path)
    # Point the recorder at its directory up front, so the panel can list past
    # runs before anything has been recorded in this process. Nothing is
    # written until a run starts, and only if recording is enabled.
    recorder.configure(config.recordings_path(), cfg["recording"]["keep_runs"])
    engine.bind(asyncio.get_running_loop())
    if cfg["audio_bus"]["enabled"]:
        # Opening devices can take a moment, and a device that is not there yet is retried by the bus itself.
        try:
            await asyncio.to_thread(audiobus.bus.apply, await asyncio.to_thread(audiobus.runtime_config, cfg["audio_bus"]))
        except Exception:
            log.exception("The audio bus could not start")
    if cfg["metering"]["enabled"]:
        try:
            await asyncio.to_thread(metering.meter.apply, cfg["metering"])
        except Exception:
            log.exception("The sound meter could not start")
    if cfg["mixreport"]["enabled"]:
        try:
            await asyncio.to_thread(mixreport.recorder.apply, cfg["mixreport"])
        except Exception:
            log.exception("The stream-mix report could not start")
    app.state.blocklist_task = asyncio.create_task(_watch_blocklist())
    app.state.alerts_task = asyncio.create_task(_alert_ticks())
    # A change of LAN address (Wi-Fi switch): mint a certificate that names the new address if the current
    # one does not; the admin page re-reads its viewer links and the fingerprint on its own.
    await asyncio.to_thread(netroute.startup)
    app.state.net_task = asyncio.create_task(netinfo.watch(_on_lan_change))
    # Tell caption panels on the LAN where Relay is (mDNS, in a background thread). Never stops Relay.
    app.state.announcer = None
    if announcemod.enabled():
        app.state.announcer = announcemod.Announcer(
            announcemod.install_name(), serving.viewer_port(cfg["port"]), os.environ.get("RELAY_VERSION") or "dev")
        app.state.announcer.start()
    # First tick runs straight away, so a relay started mid-window catches up.
    scheduler.start()
    # The integrations that are switched on (app/modules/). Each idles until it is set up.
    await module_registry.start_enabled()
    if ready and not (cfg.get("admin_token") or "").strip():
        with contextlib.suppress(Exception):
            fresh = await asyncio.to_thread(_any_users) is False
            if fresh and setupcode.enabled():
                await asyncio.to_thread(_announce_setup_code)
            elif fresh:
                log.info("No users yet: open the operator panel on this computer to finish setting up Relay.")
            elif setupcode.enabled():
                await asyncio.to_thread(setupcode.clear)
    if serving.published():
        log.info("Viewer pages on port %d (HTTP), operator panel on port %d (HTTPS)",
                 serving.viewer_port(), serving.admin_port())
        for note in serving.notices():
            log.warning(note)
    if demo_mode():
        from . import demo

        target = next(
            (t for t, s in cfg["targets"].items() if s.get("enabled")), "SPANISH"
        )
        app.state.demo_task = asyncio.create_task(demo.run(cfg["source_language"], target))
        log.warning("REHEARSAL MODE: captions are canned. No OpenAI session is open.")


async def _watch_blocklist() -> None:
    """Reload the blocklist file when it changes on disk.

    The operator edits the file directly, so the change has to land without a
    restart -- often mid-event.
    """
    path = config.blocklist_path()
    seen = redact.file_mtime(path)
    while True:
        await asyncio.sleep(BLOCKLIST_POLL_S)
        try:
            path = config.blocklist_path()
            mtime = redact.file_mtime(path)
            if mtime == seen:
                continue
            seen = mtime
            terms = redact.read_blocklist_file(path)
            if terms != hub.blocklist:
                hub.set_blocklist(terms)
                log.info("Blocked words reloaded: %d term(s)", len(terms))
                engine._push_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("blocklist reload failed")


async def _alert_ticks() -> None:
    """Once a second: end alerts nobody tapped in time, so the sender sees "Not seen" when it happens."""
    while True:
        await asyncio.sleep(1.0)
        try:
            alerts.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("alert tick failed")


def _backup_if_due() -> None:
    if not db.available():
        return
    audit.prune()                                   # the audit log keeps audit.KEEP_DAYS days
    alerthistory.prune()                            # alert history keeps alerthistory.KEEP_DAYS days
    if db.backup_due():
        db.backup(passphrase_key=config.get().get("backup_key") or "")


async def _backup_loop() -> None:
    """Dump the database when none was taken in the last day: shortly after start-up, then hourly. A dump
    that cannot be taken is logged by db.backup and tried again next hour."""
    delay = BACKUP_FIRST_DELAY_S
    while True:
        await asyncio.sleep(delay)
        delay = BACKUP_EVERY_S
        try:
            await asyncio.to_thread(_backup_if_due)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("database backup check failed")


async def _shutdown() -> None:
    with contextlib.suppress(Exception):
        await module_registry.stop_all()
    scheduler.cancel()
    announcer = getattr(app.state, "announcer", None)
    if announcer is not None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(announcer.stop)
    for name in ("demo_task", "blocklist_task", "alerts_task", "backup_task", "net_task"):
        task = getattr(app.state, name, None)
        if task is not None:
            task.cancel()
    with contextlib.suppress(Exception):
        await engine.stop()
    with contextlib.suppress(Exception):
        await asyncio.to_thread(metering.meter.stop)
    with contextlib.suppress(Exception):
        await asyncio.to_thread(mixreport.recorder.stop)
    with contextlib.suppress(Exception):
        await asyncio.to_thread(audiobus.bus.stop)
    with contextlib.suppress(Exception):
        await asyncio.to_thread(db.close)


def _urls(port: int) -> list[str]:
    """The viewer links the panel shows (and turns into QR codes).

    Relay serves viewers itself, so the links are this machine's own network addresses (app/netinfo.py)
    on the viewer port, which is left out of the link when it is 80. With the viewers routed to a network port
    (app/netroute.py), only that port's address.
    """
    ips = netroute.link_addresses("viewers") or ["127.0.0.1"]
    return [serving.link("http", ip, port) for ip in ips] + [serving.link("http", "127.0.0.1", port)]


def _serving_info() -> dict:
    """What the admin page shows about how Relay is reachable: ports, notices, panel links, fingerprint."""
    cfg = config.get()
    admin_port = serving.admin_port(cfg["admin_port"])
    ips = netroute.link_addresses("crew") or ["127.0.0.1"]
    hosts = ([cfg["admin_fqdn"]] if cfg.get("admin_fqdn") else []) + ips
    return {
        "viewer_port": serving.viewer_port(cfg["port"]),
        "admin_port": admin_port,
        "notices": serving.notices(),
        "panel_urls": [serving.link("https", h, admin_port, "/admin") for h in hosts],
        "certificate": tls.panel.describe(),
    }


# ---------------------------------------------------------------- auth
def _token_ok(supplied: str, real: str) -> bool:
    """Constant-time token compare that survives non-ASCII tokens.

    secrets.compare_digest() raises TypeError on str with non-ASCII characters,
    so an accented admin token would turn every auth check into a 500. Comparing
    the UTF-8 bytes keeps the timing safety and accepts any token the operator
    can type.
    """
    if not real or not supplied:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), real.encode("utf-8"))


# Per-client-IP login failures: ip -> (count, last failure time). Shared by password sign-in, PIN sign-in and
# first-run setup.
_LOGIN_FAILS: dict[str, tuple[int, float]] = {}

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _from_loopback(request: Request) -> bool:
    """True when the TCP peer is a process on this host.

    Only such a peer may speak for `X-Forwarded-*`. A reverse proxy in front of
    the panel connects over loopback; anything arriving from the LAN is a direct
    client and its headers are attacker-controlled, so they are ignored.
    """
    peer = request.client.host if request.client else ""
    return peer in _LOOPBACK_HOSTS


def _client_ip(request: Request) -> str:
    """The address the login rate limiter counts against.

    Behind a reverse proxy every request has a peer of 127.0.0.1, which
    would collapse the per-IP lockout added in #8 into a single global counter:
    one guesser would lock out the operator. So take the left-most entry of
    X-Forwarded-For -- but only from a loopback peer, never from a direct
    client that could simply invent the header to dodge its own lockout.
    """
    if _from_loopback(request):
        fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "unknown"


def _is_https(request: Request) -> bool:
    """True when the browser's leg of this request is TLS.

    The panel socket speaks TLS itself, so its requests say so. A reverse proxy that terminates TLS in
    front of the app (not something Relay ships) is trusted only from a loopback peer.
    """
    if request.url.scheme == "https":
        return True
    if _from_loopback(request):
        return (request.headers.get("x-forwarded-proto") or "").strip().lower() == "https"
    return False


def _login_locked(ip: str) -> float:
    """Seconds of lockout left for this IP, 0.0 when it may try again."""
    count, last = _LOGIN_FAILS.get(ip, (0, 0.0))
    if count < LOGIN_MAX_FAILS:
        return 0.0
    left = LOGIN_LOCKOUT_S - (time.monotonic() - last)
    if left <= 0:
        _LOGIN_FAILS.pop(ip, None)
        return 0.0
    return left


def _login_failed(ip: str) -> None:
    count, last = _LOGIN_FAILS.get(ip, (0, 0.0))
    # A quiet lockout window clears the slate rather than accumulating forever.
    if count and time.monotonic() - last > LOGIN_LOCKOUT_S:
        count = 0
    _LOGIN_FAILS[ip] = (count + 1, time.monotonic())
    log.warning("Admin login failed from %s (%d in a row)", ip, count + 1)


async def _fail(ip: str) -> None:
    """Count one failed attempt and make it cost a fixed delay: under a second for an operator who mistyped,
    and it caps a guesser on the venue LAN long before the lockout bites. The attempt that starts a lockout
    is written to the audit log (the ones refused during it are not: they would only fill it)."""
    await asyncio.sleep(LOGIN_FAIL_DELAY_S)
    _login_failed(ip)
    if _LOGIN_FAILS.get(ip, (0, 0.0))[0] == LOGIN_MAX_FAILS:
        await asyncio.to_thread(
            audit.record, "auth.locked",
            f"Sign-in from this address locked for {int(LOGIN_LOCKOUT_S) // 60} min after "
            f"{LOGIN_MAX_FAILS} failed attempts", ip=ip)


def _locked_sentence(left: float) -> str:
    return f"Too many attempts. Try again in {int(left) // 60 + 1} min."


def _set_cookie(resp: Response, request: Request, name: str, token: str, max_age: int) -> None:
    # `Secure` only when this request actually came over TLS: setting it on a plain-HTTP login would have the
    # browser drop the cookie and lock the operator out of a perfectly good loopback or single-port setup.
    resp.set_cookie(name, token, httponly=True, samesite="strict", secure=_is_https(request),
                    max_age=max_age, path="/")


# ----------------------------------------------------- the database, and whether anyone has an account
_db_ready = False
# Set once users.count() > 0 has been seen. It can only become false again by wiping the database (the
# lockout tool, a test's reset), which is why those call forget_users_cache().
_have_users = False
# One first-run setup at a time, so two valid posts arriving together cannot both create an Owner.
_SETUP_LOCK = threading.Lock()

# The caller of a first-run request that sent the admin token as `x-admin-token`: the setup token acts as an
# Owner while there are no accounts. It has no row (id 0), so anything that needs an account refuses it.
SETUP_OWNER = User(id=0, username="setup", name="Setup token", role="owner", disabled=False,
                   must_change_password=False, pin_set=False)

DB_DOWN_SENTENCE = "The user database is not available. Relay is still captioning; try again in a moment."

# Web-layer sentences that are not users.MESSAGES. Fixed text, looked up by key.
WEB_MESSAGES = {
    "bad_token": "That admin token is not right.",
    "bad_code": "That setup code is not right.",
    "local_only": "Finish setting up Relay on the computer it runs on: open this page there.",
    "already_set_up": "Relay is already set up. Sign in instead.",
    "mismatch": "The two passwords do not match.",
    "bad_request": "That request is not valid.",
    "signin_required": "Sign in required",
    "not_allowed": "Not allowed",
    "backup_passphrase_short": "The backup passphrase needs at least 10 characters.",
    "bad_api_token": "That API token is not valid, has expired, or its user is turned off.",
}


class _WebRefused(Exception):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key

    @property
    def message(self) -> str:
        return WEB_MESSAGES[self.key]


def _ready_quietly() -> bool:
    global _db_ready
    _db_ready = bool(db.init())
    return _db_ready


def _ready() -> None:
    """Raise db.Unavailable unless the database is up and migrated (it is retried here after a failed
    start-up, and db backs off between probes, so a dead database costs nothing per request)."""
    if _db_ready:
        return
    if not _ready_quietly():
        raise db.Unavailable("The database is unavailable.")


def _any_users() -> bool:
    """True once any account exists. Blocks (database)."""
    global _have_users
    if _have_users:
        return True
    _ready()
    _have_users = users.count() > 0
    return _have_users


def forget_users_cache() -> None:
    """Forget that accounts were seen: for whatever wipes the database (tests)."""
    global _have_users
    _have_users = False


def _session_token(request: Request) -> str:
    return request.cookies.get(ADMIN_COOKIE) or ""


def _session_user(request: Request) -> User | None:
    """The signed-in user for this request's session cookie, loaded fresh (so a disabled or deleted user, or
    a changed role, takes effect on the next request). Blocks (database)."""
    global _have_users
    _ready()
    token = _session_token(request)
    user = users.session_user(token) if token else None
    if user is not None:
        _have_users = True
    return user


def _bearer(request: Request) -> str | None:
    """The credential of an `Authorization: Bearer …` header, "" for any other Authorization header, None
    when there is none."""
    auth = request.headers.get("authorization")
    if auth is None:
        return None
    scheme, _, cred = auth.strip().partition(" ")
    return cred.strip() if scheme.lower() == "bearer" else ""


def require(perm: str | None, *, pending_ok: bool = False, tokens: bool = True):
    """FastAPI dependency factory: the request needs a signed-in user who holds `perm` (None: just signed in).

    401 {"detail": "Sign in required"} when not signed in; 403 "password_change_required" while the user must
    still change their password (unless `pending_ok`: only /me, the password change and logout); 403
    "Not allowed" when the role lacks the permission; 503 when the database is down. `x-admin-token` is
    honoured only while no account exists, and then acts as an Owner. It is a sync dependency, so the database
    calls run in the thread pool. `request.state.user` is the User.

    An `Authorization: Bearer` API token (#174) is used instead of the cookie when one is sent: it acts as its
    user less users.TOKEN_BARRED, and `tokens=False` refuses it outright (the signed-in user's own password,
    PIN and tokens, which have no permission of their own to bar). A bad token is a 401, never a fall-back to
    the cookie.
    """
    def dep(request: Request) -> User:
        bearer = _bearer(request)
        if bearer is not None:
            _ready()
            user = users.token_user(bearer)
            if user is None:
                raise HTTPException(status_code=401, detail=WEB_MESSAGES["bad_api_token"])
            if not tokens:
                raise HTTPException(status_code=403, detail=WEB_MESSAGES["not_allowed"])
        else:
            user = _session_user(request)
        if user is None:
            supplied = request.headers.get("x-admin-token") or ""
            real = (config.get().get("admin_token") or "").strip()
            if supplied and not _any_users() and _token_ok(supplied, real):
                user = SETUP_OWNER
        if user is None:
            raise HTTPException(status_code=401, detail=WEB_MESSAGES["signin_required"])
        if user.must_change_password and not pending_ok:
            raise HTTPException(status_code=403, detail="password_change_required")
        if perm and not user.can(perm):
            raise HTTPException(status_code=403, detail=WEB_MESSAGES["not_allowed"])
        request.state.user = user
        return user

    dep.permission = perm        # type: ignore[attr-defined]  (the permission test reads it)
    dep.tokens = tokens          # type: ignore[attr-defined]
    return dep


# Modules' admin routes use the same checks, and their pages the same templates (they cannot import this file).
modules_pkg.permission_hook = require
modules_pkg.templates = templates

# What a refusal from users.py means as a status. The text sent is always Refused.message.
_REFUSAL_STATUS = {
    "bad_login": 401, "wrong_pin": 401, "pin_cleared": 401, "no_device": 401,
    "disabled": 403, "not_allowed": 403, "role_too_high": 403, "account_above_you": 403, "self_action": 403, "wrong_password": 403,
    "not_found": 404,
    "username_taken": 409, "last_owner": 409,
}


def _refusal_status(exc: users.Refused) -> int:
    return _REFUSAL_STATUS.get(exc.key, 400)


@app.exception_handler(users.Refused)
async def _on_refused(request: Request, exc: users.Refused):
    return JSONResponse({"detail": exc.message}, status_code=_refusal_status(exc))


@app.exception_handler(db.Unavailable)
async def _on_db_down(request: Request, exc: db.Unavailable):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": DB_DOWN_SENTENCE}, status_code=503)
    return templates.TemplateResponse(request, "db_down.html", {"message": DB_DOWN_SENTENCE}, status_code=503,
                                      headers={"Cache-Control": "no-store"})


def _bad_request() -> HTTPException:
    return HTTPException(status_code=400, detail=WEB_MESSAGES["bad_request"])


# ------------------------------------------------------------- viewers
def _viewer_ctx(request: Request) -> dict:
    cfg = config.get()
    src = cfg["source_language"]
    return {
        "request": request,
        "font_px": cfg["default_font_px"],
        "source_language": src,
        "source_label": languages.SOURCE_LANGUAGES[src]["label"],
        "source_iso": languages.SOURCE_LANGUAGES[src]["iso"],
        "present": request.query_params.get("present") in ("1", "true", "yes"),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    ctx = _viewer_ctx(request) | {"show_admin": _on_admin_port(request)}
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/transcription", response_class=HTMLResponse)
async def page_transcription(request: Request):
    ctx = _viewer_ctx(request) | {"mode": "transcription"}
    return templates.TemplateResponse(request, "viewer.html", ctx)


@app.get("/translation", response_class=HTMLResponse)
async def page_translation(request: Request):
    ctx = _viewer_ctx(request) | {"mode": "translation"}
    return templates.TemplateResponse(request, "viewer.html", ctx)


@app.get("/both", response_class=HTMLResponse)
async def page_both(request: Request):
    ctx = _viewer_ctx(request) | {"mode": "both"}
    return templates.TemplateResponse(request, "viewer.html", ctx)


@app.get("/present", response_class=HTMLResponse)
async def page_present(request: Request):
    """Projector / confidence-monitor view: chrome hidden, bottom-anchored, big
    type. Defaults to the translation feed; ?mode= picks another. Present is also
    reachable as ?present=1 on any viewer route (see _viewer_ctx)."""
    mode = request.query_params.get("mode", "translation")
    if mode not in ("transcription", "translation", "both"):
        mode = "translation"
    ctx = _viewer_ctx(request) | {"mode": mode, "present": True}
    return templates.TemplateResponse(request, "viewer.html", ctx)


@app.get("/screen", response_class=HTMLResponse)
async def page_screen(request: Request):
    """Kiosk / house-display view: no chrome, no picker, no scrollbar. The whole
    configuration surface is the URL, because the operator types it into a kiosk
    browser once and walks away. Reuses viewer.html; the
    template branches on `screen` for the no-chrome, fixed-language, capped-feed
    layout. Not offered on the chooser page -- this is operator setup, not an
    audience choice."""
    qp = request.query_params
    # A page a switched-on module serves on /screen (?lighting=<page>, #379), chosen by its query parameter.
    for name, hook in modules_pkg.using_all("screen_pages.").items():
        if qp.get(name):
            resp = await hook(request)
            if resp is not None:
                return resp
    # ?rundown=full|side|micboard: the production team's rundown from a module that offers the "rundown" stream
    # (Planning Center, #148), in place of the captions or beside them.
    view = qp.get("rundown") or ""
    if view in RUNDOWN_VIEWS and RUNDOWN_STREAM in module_registry.streams():
        return _rundown_page(request, view)
    stream = qp.get("stream", "translation")
    if stream not in ("transcription", "translation", "both"):
        stream = "translation"

    # Fixed target: an explicit ?lang= wins; otherwise resolve to the first live
    # target the same way the picker's default does. There is nobody at the
    # screen to choose one.
    fixed_lang = (qp.get("lang") or "").upper()
    if fixed_lang not in languages.TARGETS:
        live = engine.live_targets()
        fixed_lang = live[0]["target"] if live else ""
    screen_iso = languages.target_iso(fixed_lang) if fixed_lang else ""

    try:
        lines = max(1, int(qp.get("lines", "3")))
    except (TypeError, ValueError):
        lines = 3

    font_override = None
    if qp.get("font"):
        try:
            font_override = max(8, int(qp.get("font")))
        except (TypeError, ValueError):
            font_override = None

    align = qp.get("align", "bottom")
    if align not in ("bottom", "center"):
        align = "bottom"

    # ?alert=<name>: this screen flashes for the alert buttons that name it (#121).
    screen_alert = alertsmod.clean_name(qp.get("alert") or "")

    ctx = _viewer_ctx(request) | {
        "mode": stream,
        "screen": True,
        "screen_lang": fixed_lang,
        "screen_iso": screen_iso,
        "screen_lines": lines,
        "screen_align": align,
        "screen_font": font_override,
        "screen_alert": screen_alert,
        # ?lyrics=1: lyrics in place of the captions while a song is up, from a module that offers the
        # "lyrics" stream (ProPresenter), and only while it is on.
        "lyrics_for": "screen" if qp.get("lyrics") in ("1", "true", "yes") and _lyrics_offered() else "",
    }
    if font_override:
        ctx["font_px"] = font_override
    return templates.TemplateResponse(request, "viewer.html", ctx)


@app.get("/status", response_class=HTMLResponse)
async def page_status(request: Request):
    """The status screen (#224): capture, input, live languages, viewers and the livestream, for the sound desk
    and the booth. Read-only, from the same feed a panel on the Status feed reads."""
    return templates.TemplateResponse(request, "status.html", _viewer_ctx(request))


@app.get("/tally", response_class=HTMLResponse)
async def page_tally(request: Request):
    """The tally screens (#245): `?camera=<n>` is a phone on a camera (red on program, green on preview),
    `?view=operator&camera=<n>` the camera operator's, `?view=board` every camera for the director. All three read
    the shared tally state, whichever module fills it."""
    qp = request.query_params
    view = qp.get("view") if qp.get("view") in tally.VIEWS else "camera"
    try:
        camera = max(0, min(999, int(qp.get("camera", "0"))))
    except (TypeError, ValueError):
        camera = 0
    return templates.TemplateResponse(request, "tally.html", _viewer_ctx(request) | {"view": view, "camera": camera})


RUNDOWN_VIEWS = ("full", "side", "micboard")


def _rundown_page(request: Request, view: str):
    """/screen?rundown=...: a fixed 1920x1080 board scaled to the screen; nothing scrolls and nothing is pressed.
    The side view shows this same /screen's captions (the URL without the rundown options) in a frame."""
    qp = request.query_params
    pick = lambda k, allowed, d: qp.get(k) if qp.get(k) in allowed else d  # noqa: E731
    caps = {k: v for k, v in qp.items() if k not in ("rundown", "layout", "cards", "team", "time", "type")}
    ctx = _viewer_ctx(request) | {
        "rundown_view": view,
        "rundown_layout": pick("layout", ("fit", "compact"), "fit"),
        "rundown_cards": pick("cards", ("person", "position"), "person"),
        "rundown_team": (qp.get("team") or "")[:80],
        "rundown_time": qp.get("time") if (qp.get("time") or "").isdigit() and len(qp.get("time")) == 4 else "",
        "rundown_type": qp.get("type") if (qp.get("type") or "").isdigit() else "",
        "rundown_captions": "/screen?" + urlencode(caps) if view == "side" else "",
    }
    return templates.TemplateResponse(request, "rundown.html", ctx)


def _lyrics_offered() -> bool:
    return LYRICS_STREAM in module_registry.streams()


def _overlay_feed(stream: str, target: str) -> dict:
    """One caption feed on an overlay: a translation into `target`, or the
    original speech when `stream` is transcription."""
    if stream == "transcription":
        src = config.get()["source_language"]
        iso = languages.SOURCE_LANGUAGES[src]["iso"]
        return {"stream": stream, "lang": src, "iso": iso,
                "label": overlays.NATIVE.get(iso, languages.SOURCE_LANGUAGES[src]["label"])}
    iso = languages.target_iso(target) if target else ""
    return {"stream": stream, "lang": target, "iso": iso,
            "label": overlays.NATIVE.get(iso, languages.target_label(target)) if target else ""}


def _overlay_page(request: Request, s: dict) -> HTMLResponse:
    """Render one overlay from resolved settings (overlays.resolve)."""
    # Fixed target: an explicit lang wins, else the first live target -- there
    # is nobody at a switcher to pick one (same resolution as /screen).
    lang = s["lang"]
    if not lang:
        live = engine.live_targets()
        lang = live[0]["target"] if live else ""
    if s["stream"] == "transcription":
        feeds = [_overlay_feed("transcription", "")]
    elif s["stream"] == "both":
        # Two stacked feeds: the first language over the second, which is the
        # original speech unless a second translation is named.
        second = (_overlay_feed("translation", s["lang2"]) if s["lang2"] and s["lang2"] != lang
                  else _overlay_feed("transcription", ""))
        feeds = [_overlay_feed("translation", lang), second]
    else:
        feeds = [_overlay_feed("translation", lang)]
    w, h = s["w"], s["h"]
    ctx = _viewer_ctx(request) | {
        "mode": s["stream"],
        "overlay": True,
        "ov": s,
        "overlay_feeds": feeds,
        "overlay_labels": s["labels"] and len(feeds) > 1,
        # Title-safe inset resolved to px on the fixed canvas, so the layout does
        # not depend on the browser-source window matching the canvas size.
        "overlay_pad_x": round(w * s["safe"] / 100),
        "overlay_pad_y": round(h * s["safe"] / 100),
        "overlay_platec": ",".join(str(int(s["platec"][i:i + 2], 16)) for i in (0, 2, 4)),
        # The admin builder's preview: sample captions played in the page, no
        # subscription, so the preview is the real renderer with or without a
        # service running.
        "overlay_demo": ({f["lang"]: overlays.SAMPLES.get(f["iso"], overlays.SAMPLES["en"]) for f in feeds}
                         if request.query_params.get("demo") in ("1", "true", "yes") else None),
        "lyrics_for": ("overlay" if request.query_params.get("lyrics") in ("1", "true", "yes")
                       and _lyrics_offered() else ""),
    }
    return templates.TemplateResponse(request, "viewer.html", ctx)


@app.get("/overlay", response_class=HTMLResponse)
async def page_overlay(request: Request):
    """Broadcast overlay: a transparent, fixed-canvas, caret-free caption feed
    meant to be composited over live video -- an OBS/vMix/ProPresenter browser
    source, or keyed over program on an ATEM. Reuses viewer.html; the template
    branches on `overlay`. Not on the chooser page -- this is switcher setup,
    not an audience choice. Every option is in the URL (overlays.DEFAULTS); the
    admin page's builder writes it, or saves it by name (/overlay/<id>)."""
    return _overlay_page(request, overlays.resolve(request.query_params, languages.TARGETS))


@app.get("/overlay/{oid}", response_class=HTMLResponse)
async def page_named_overlay(oid: str, request: Request):
    """A saved overlay. Its settings live in config, so an edit in admin reaches
    the browser source on its next load with no new link to paste; anything in
    the query string (?lyrics=1, or a one-off tweak) still applies on top."""
    saved = next((o for o in config.get()["overlays"] if o["id"] == oid), None)
    if saved is None:
        raise HTTPException(404, "unknown overlay")
    raw = dict(saved["settings"]) | dict(request.query_params)
    return _overlay_page(request, overlays.resolve(raw, languages.TARGETS))


@app.get("/api/targets")
async def api_targets():
    """First-paint + resync endpoint for the viewer picker. The live set is now
    pushed over the caption SSE (see engine.targets_payload); this stays for the
    initial load and the visibilitychange resync. Live targets only -- the picker
    never offers a dead language."""
    data = engine.targets_payload()
    data.pop("type", None)
    if not data["live"] and demo_mode():
        cfg = config.get()
        data["live"] = [
            {
                "target": name,
                "label": languages.target_label(name),
                "iso": languages.target_iso(name),
                "script": languages.target_script(name),
                "state": "demo",
            }
            for name, spec in cfg["targets"].items()
            if spec.get("enabled")
        ]
        data["running"] = True
    return data


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _parse_seq(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


ALERT_STREAM = "alert"
# Served by a module (ProPresenter) while it is on; the viewer's lyrics.js reads it.
LYRICS_STREAM = "lyrics"
# Served by a module (Planning Center) while it is on and the operator allows it; the screen's rundown.js reads it.
RUNDOWN_STREAM = "rundown"


def _alert_sender_stream() -> StreamingResponse:
    """Every alert button and how it stands, whole in each message (alerts.py): for the /alert page and a panel
    on the Alert feed. Repeated at each heartbeat, so a panel's silence timer never fires on a quiet stream."""
    queue = alerts.subscribe_sender()

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield alertsmod.sse_frame(alerts.state())
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            alerts.unsubscribe_sender(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/stream")
async def stream(request: Request, stream: str = "translation", lang: str | None = None,
                 finals: int = 0, type: str | None = None, sound: int = 0):
    if stream == ALERT_STREAM:
        return _alert_sender_stream()
    if stream == streamstatus.STREAM:
        return StreamingResponse(
            streamstatus.frames(sound=bool(sound)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
    if stream == tally.STREAM:
        return StreamingResponse(
            tally.frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
    # A stream a module owns (only while it is on).
    params = dict(request.query_params)
    params.update({k: v for k, v in (("type", type), ("lang", lang)) if v is not None})
    owned = module_registry.stream(stream, request, params)
    if owned is not None:
        return owned
    if stream not in (TRANSCRIPTION_STREAM, TRANSLATION_STREAM):
        raise HTTPException(400, "unknown stream")
    if stream == TRANSCRIPTION_STREAM:
        lang = config.get()["source_language"]
    else:
        lang = (lang or "").upper()
        if lang not in languages.TARGETS:
            raise HTTPException(400, "unknown language")

    # Resume point: the browser's Last-Event-ID header on a native reconnect, or
    # an explicit ?last_id= the viewer sends when it reconnects by hand. Either
    # lets the hub replay only what was missed instead of a full snapshot.
    last_id = _parse_seq(request.headers.get("last-event-id") or request.query_params.get("last_id"))
    channel, queue = await hub.subscribe(stream, lang, last_id, finals_only=bool(finals))

    async def gen():
        # The queue already holds finished SSE frames (serialised once at
        # publish). No per-message is_disconnected() poll: the generator's
        # finally unsubscribes when the client drops and the response is
        # cancelled -- at worst one heartbeat later.
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                yield frame
        finally:
            hub.unsubscribe(channel, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------- panels
# Caption panels (firmware/relay-display) talk to these two routes on the viewer
# port, with no login: they do not pair. See panels.py for what bounds them.
# Shared with modules (app/modules/__init__.py), which cannot import this file.
_read_limited = modules_pkg.read_limited


def _panel_screen(pid, panel) -> None:
    """A panel that checks in is a screen of type panel (app/screens.py), named as the Panels page names it."""
    if panel is None:
        return
    try:
        core = screens.adopt("panel:" + pid, name=panel.name, kind="panel", address=panel.ip)
        screens.sync_panel_name(core["id"], panel.name)
    except (db.Unavailable, screens.Refused):
        log.warning("A panel checked in but could not be listed as a screen")   # the check-in itself still succeeds


@app.post("/api/panels/checkin")
async def panel_checkin(request: Request):
    raw = await _read_limited(request, panels.CHECKIN_MAX_BYTES)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    peer = request.client.host if request.client else ""
    try:
        reply = panel_registry.checkin(body, peer)
        await asyncio.to_thread(_panel_screen, body.get("id"), panel_registry.panels.get(body.get("id")))
        return reply
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except panels.RegistryFull:
        log.warning("Panel registry full (%d); refused a check-in from %s",
                    panels.MAX_PANELS, peer)
        raise HTTPException(429, "too many panels")


@app.get("/api/panels/{panel_id}/events")
async def panel_events(panel_id: str):
    # Only a panel that has checked in. The stream carries nothing but a feed
    # choice, so this is bookkeeping, not access control.
    if not panels.valid_id(panel_id) or panel_id not in panel_registry.panels:
        raise HTTPException(404, "unknown panel")
    queue = panel_registry.subscribe(panel_id)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            panel_registry.unsubscribe(panel_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )





# --------------------------------------------------------------- alerts (#121)
# On the viewer port with no login, like the panels' routes: anyone with the /alert link may send, by the
# operator's choice, unless the operator requires a sign-in (#175). alerts.py says what bounds them.
alerts.push_panel = panel_registry.push_alert
alerts.panel_name = panel_registry.display_name
panel_registry.alerts_for = alerts.active_for_panel


async def _alert_body(request: Request) -> dict:
    raw = await _read_limited(request, alertsmod.BODY_MAX_BYTES)
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


def _alert_refused(exc: alertsmod.Refused) -> JSONResponse:
    # A fixed sentence by key, never the exception's own text.
    return JSONResponse({"ok": False, "error": alertsmod.MESSAGES.get(exc.key, "The alert could not be sent.")},
                        status_code=exc.status)


def _alert_signin_required() -> bool:
    return bool(alerts.settings().get("require_signin"))


_alert_send_check = require("alerts.send")


async def _alert_account(request: Request) -> User:
    """With sign-in required, a phone sends only as an account holding alerts.send, checked by the same call as
    every admin route, and only over the operator port (HTTPS), where the session cookie lives (#175)."""
    if not _on_admin_port(request):
        raise HTTPException(status_code=401, detail=WEB_MESSAGES["signin_required"])
    return await asyncio.to_thread(_alert_send_check, request)


@app.get("/alert", response_class=HTMLResponse)
async def page_alert(request: Request):
    """The musicians' page: one button per alert. Not on the chooser page; the operator hands out the link.
    With sign-in required it is served only on the operator port, to a signed-in account holding alerts.send."""
    if not _alert_signin_required():
        return templates.TemplateResponse(request, "alert.html", _viewer_ctx(request) | {"account": None})
    if not _on_admin_port(request):
        host = request.url.hostname or "localhost"
        if ":" in host:
            host = f"[{host}]"
        return RedirectResponse(serving.link("https", host, serving.admin_port(config.get()["admin_port"]),
                                             "/alert"), status_code=303)
    got = await asyncio.to_thread(_page_gate, request, "alerts.send")
    if not isinstance(got, User):
        return got
    return templates.TemplateResponse(request, "alert.html", _viewer_ctx(request) | {"account": got.name},
                                      headers=_NO_STORE)


@app.post("/api/alerts/messages/send")
async def alert_message_send(request: Request):
    """Show a stage message (#214): `preset` is a preset's id, or `text` is words typed on the /alert page (`banner`
    keeps them up for the banner time). `from` names the sender, as for an alert."""
    body = await _alert_body(request)
    sender = alertsmod.clean_name(body.get("from"))
    if _alert_signin_required():
        sender = alertsmod.clean_name((await _alert_account(request)).name)
    try:
        return {"ok": True, **alerts.send_message(sender, preset=body.get("preset"), text=body.get("text"),
                                                  banner=body.get("banner"), level=body.get("level"))}
    except alertsmod.Refused as exc:
        return _alert_refused(exc)


@app.post("/api/alerts/messages/clear")
async def alert_message_clear(request: Request):
    """Take a stage message down (`id`), or all of them when no id is given."""
    if _alert_signin_required():
        await _alert_account(request)
    body = await _alert_body(request)
    mid = body.get("id")
    if mid is not None and not isinstance(mid, str):
        raise HTTPException(400, "id must be a message id")
    return {"ok": True, **alerts.clear_messages(mid)}


@app.get("/api/alerts/messages/stream")
async def alert_messages_stream():
    """The /alert page's stage messages: the presets, and what is showing now, whole in each message."""
    queue = alerts.subscribe_messages()

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield alertsmod.sse_frame(alerts.message_state())
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            alerts.unsubscribe_messages(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/alerts/{button_id}/send")
async def alert_send(button_id: str, request: Request):
    """Flash a button's screens, or stop the alert it has flashing. `from` names the sender; a panel sends its
    `panel` id instead, and is named as Relay names it. With sign-in required, a phone is named by its account
    and `from` is ignored."""
    body = await _alert_body(request)
    sender = alertsmod.clean_name(body.get("from"))
    pid = body.get("panel")
    if pid is not None:
        if not panels.valid_id(pid) or not panel_registry.known(pid):
            raise HTTPException(404, "unknown panel")
        sender = panel_registry.display_name(pid)
    elif _alert_signin_required():
        sender = alertsmod.clean_name((await _alert_account(request)).name)
    try:
        return {"ok": True, **alerts.press(button_id, sender, body.get("level"))}
    except alertsmod.Refused as exc:
        return _alert_refused(exc)


@app.post("/api/alerts/{button_id}/cancel")
async def alert_cancel(button_id: str, request: Request):
    if _alert_signin_required():
        await _alert_account(request)
    try:
        return {"ok": True, **alerts.cancel(button_id)}
    except alertsmod.Refused as exc:
        return _alert_refused(exc)


@app.post("/api/alerts/ack")
async def alert_ack(request: Request):
    """A flashing panel or screen was tapped: `id` is the alert, and `panel` (an id) or `screen` (a name) says
    where."""
    body = await _alert_body(request)
    panel = body.get("panel")
    screen = body.get("screen")
    if panel is not None and not panels.valid_id(panel):
        raise HTTPException(400, "panel must be a panel id")
    if screen is not None and not isinstance(screen, str):
        raise HTTPException(400, "screen must be a name")
    try:
        return alerts.ack(body.get("id"), panel=panel, screen=screen, reply=body.get("reply"))
    except alertsmod.Refused as exc:
        return _alert_refused(exc)


@app.get("/api/alerts/screen")
async def alert_screen_stream(name: str = ""):
    """A browser screen's alerts: `alert` when one of its buttons is pressed, `alert_end` when it stops."""
    name = alertsmod.clean_name(name)
    if not name:
        raise HTTPException(400, "name the screen")
    try:
        queue = alerts.subscribe_screen(name)
    except alertsmod.Refused as exc:
        raise HTTPException(exc.status, alertsmod.MESSAGES.get(exc.key, "Refused."))
    # An alert screen is a core screen (#527): the one called this name, or a new one, so every page lists it once.
    try:
        await asyncio.to_thread(screens.adopt, "alert:" + name.lower(), name=name)
    except (db.Unavailable, screens.Refused):
        log.warning("An alert screen opened but could not be listed as a screen")

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if frame is None:
                    return
                yield frame
        finally:
            alerts.unsubscribe_screen(name, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/api/panels/firmware/{board}/{name}")
async def panel_firmware_image(board: str, name: str):
    """A board's stored panel image, to a panel that was told to fetch it. Plain HTTP on the LAN, like
    the rest of the panels' traffic: what keeps a stranger's image off a panel is the signature the
    panel checks, not this route. Only the version held is served, under the name the offer gave."""
    version = name[:-4] if name.endswith(".bin") else ""
    path = (panelfw.image_path_for(version, board)
            if panelfw.parse_version(version) and board in panelfw.BOARDS else None)
    if path is None:
        raise HTTPException(404, "no such firmware")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


@app.post("/api/panels/{panel_id}/update")
async def panel_update_report(panel_id: str, request: Request):
    """A panel's progress and result on a firmware update. Bounded like a check-in: nothing here is
    authenticated, so only what the admin page shows is kept, and it is in memory."""
    raw = await _read_limited(request, panels.UPDATE_REPORT_MAX_BYTES)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    if not panel_registry.report_update(panel_id, body):
        raise HTTPException(404, "unknown panel")
    return {"ok": True}


# --------------------------------------------------------------- admin
# Server-rendered pages and forms (they work without JavaScript), then the JSON API under /api/admin.
_NO_STORE = {"Cache-Control": "no-store"}


def _render(request: Request, template: str, ctx: dict, status: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, template, ctx, status_code=status, headers=_NO_STORE)


def _login_page(request: Request, *, error: str | None = None, status: int = 200, username: str = "",
                pw: bool = False, pin_user: int | None = None, setup: dict | None = None,
                notice: str | None = None) -> HTMLResponse:
    """The sign-in form, the first-run form (no accounts yet) or, on a trusted device with PIN users, the PIN
    picker. `pw` forces the password form. Blocks (database). Passwords are never echoed back; a username is."""
    if setup is not None or not _any_users():
        form = {"username": "", "name": ""} | (setup or {})
        token_set = bool((config.get().get("admin_token") or "").strip())
        code_set = not token_set and bool(setupcode.active())
        return _render(request, "login.html", {"mode": "setup", "error": error, "notice": notice, **form, "token_set": token_set,
                                               "code_set": code_set,
                                               "remote": not token_set and not code_set and not _setup_local(request)}, status)
    device = users.device_for(request.cookies.get(DEVICE_COOKIE) or "")
    pins = users.pin_users() if device else []
    mode = "pin" if pins and not pw else "signin"
    return _render(request, "login.html", {"mode": mode, "error": error, "username": username,
                                           "pin_users": pins, "pin_user": pin_user,
                                           "device_name": device["name"] if device else ""}, status)


def _announce_setup_code() -> None:
    """Headless install, no accounts yet: print the one-time setup code to the log (the journal). The installer
    reads the same code from the state folder. Blocks (file)."""
    pending = setupcode.prepare(have_users=False)
    if not pending:
        return
    lines = ["", "  Relay is waiting for its first Owner. From any device on this network open the operator panel",
             "  and enter this setup code:", f"      {pending}", "  (shown again by: journalctl -u relay | grep -A1 'setup code')", ""]
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def _password_page(request: Request, *, error: str | None = None, status: int = 200,
                   voluntary: bool = False) -> HTMLResponse:
    """The password form: forced while a temporary password is pending, or `voluntary` from the home page (the
    operator panel has its own, under My account)."""
    return _render(request, "password_change.html", {"error": error, "voluntary": voluntary}, status)


def _denied_page() -> HTMLResponse:
    return HTMLResponse("<!doctype html><title>Not allowed</title><p>Not allowed.</p>", status_code=403,
                        headers=_NO_STORE)


def _page_gate(request: Request, perm: str | None = None, *, pw: bool = False):
    """For a server-rendered admin page: the signed-in User, or the response to send instead (first-run form,
    sign-in, forced password change, or "not allowed"). Blocks (database)."""
    if not _any_users():
        return _login_page(request)
    user = _session_user(request)
    if user is None:
        return _login_page(request, pw=pw)
    if user.must_change_password:
        return _password_page(request)
    if perm and not user.can(perm):
        return _denied_page()
    return user


modules_pkg.page_gate = _page_gate


def _me_payload(request: Request, user: User) -> dict:
    """Who is signed in and what they may do: GET /api/admin/me, and `me` on the admin page."""
    device = users.device_for(request.cookies.get(DEVICE_COOKIE) or "")
    return {
        "user": {"id": user.id, "username": user.username, "name": user.name, "role": user.role,
                 "must_change_password": user.must_change_password},
        "permissions": sorted(user.permissions()),
        "pin_set": user.pin_set,
        "device": {"trusted": device is not None, "name": device["name"] if device else None},
        "prefs": prefs.all_prefs(user.id) if user.id else {},
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, pw: str = ""):
    got = _page_gate(request, pw=pw == "1")
    if not isinstance(got, User):
        return got
    if not got.can("admin.open"):
        return RedirectResponse(HOME_PATH, status_code=303)
    return templates.TemplateResponse(request, "admin.html", {
        "me": _me_payload(request, got),
        "module_nav": module_registry.admin_nav(got),
        "module_tabs": module_registry.admin_tabs(got),
        "module_scripts": module_registry.admin_scripts(got),
        "module_styles": module_registry.admin_styles(got),
        "look": prefs.shell_view(prefs.scope_prefs(got.id) if got.id else {}),
    }, headers=_NO_STORE)


@app.get("/admin/flash", response_class=HTMLResponse)
def admin_flash_page(request: Request, board: str = ""):
    """Write a new or recovered panel's whole firmware over USB from the browser (copied from VXP's
    board flasher). The page holds every sentence it can show; `flasher.js` picks which is visible."""
    got = _page_gate(request, "panels.manage")
    if not isinstance(got, User):
        return got
    boards = panelfw.active_boards()
    chosen = next((b for b in boards if b.id == board), boards[0])
    info = panelfw.current_flash(chosen.id)
    return templates.TemplateResponse(request, "flash.html", {
        "boards": [{"id": b.id, "label": b.label, "flash": panelfw.current_flash(b.id)} for b in boards],
        "board": chosen, "flash_mb": panelfw.FLASH_MB.get(chosen.id, 16),
        "project": panelfw.PROJECT_NAME, "flash": info,
    })


HOME_PATH = "/home"


def _home_for(user: User) -> str:
    """Where a sign-in lands: the operator panel for a role that may open it, else the home page listing the
    features the account may use (#175)."""
    return "/admin" if user.can("admin.open") else HOME_PATH


# The features a signed-in account can open outside the operator panel: core's, then the modules' (#175).
# (title, what it is for, path, permission).
CORE_HOME_TOOLS = (
    ("Alerts", "Flash a screen to get someone's attention", "/alert", "alerts.send"),
)


def _home_tools(user: User) -> list[dict]:
    tools = [{"title": t, "note": n, "path": p} for t, n, p, perm in CORE_HOME_TOOLS if user.can(perm)]
    return tools + module_registry.user_nav(user)


@app.get(HOME_PATH, response_class=HTMLResponse)
def home_page(request: Request, password: str = ""):
    """Where an account that never opens the operator panel lands after signing in: the features it may use,
    its password and sign-out. Anyone signed in may open it. ?password=1 is the password form."""
    got = _page_gate(request)
    if not isinstance(got, User):
        return got
    if password == "1":
        return _password_page(request, voluntary=True)
    return _render(request, "home.html", {"user": got, "tools": _home_tools(got),
                                          "admin": got.can("admin.open")})


def _signed_in(request: Request, started: tuple[str, int, str]) -> RedirectResponse:
    """`started` is (session token, max age, where to land)."""
    token, max_age, dest = started
    resp = RedirectResponse(dest, status_code=303)
    _set_cookie(resp, request, ADMIN_COOKIE, token, max_age)
    return resp


def _setup_local(request: Request) -> bool:
    """True when first-run setup may be done from this request: it comes from the machine itself.

    Behind a reverse proxy the peer is always loopback, so this judges the client the proxy reports
    (X-Forwarded-For, trusted only from a loopback peer; see _client_ip), not the peer.
    """
    return _client_ip(request) in _LOOPBACK_HOSTS | {"::ffff:127.0.0.1"}


def _field(value: str, limit: int = 200) -> str:
    return (value or "")[:limit]


@app.post("/admin/setup")
async def admin_setup(request: Request, token: str = Form(""), username: str = Form(""), name: str = Form(""),
                      password: str = Form(""), confirm: str = Form(""), api_key: str = Form("")):
    """First run creates the Owner (and, optionally, stores the OpenAI key), and signs them in.

    With no admin token in config.json the page works only from the machine itself. A config.json that does
    carry an admin token (a container install) keeps requiring it, checked in constant time. Once any account
    exists this is never accepted again."""
    ip = _client_ip(request)
    username, name = _field(username, 100), _field(name, 200)
    shown = {"username": username, "name": name}
    left = _login_locked(ip)
    if left:
        return await asyncio.to_thread(_login_page, request, error=_locked_sentence(left), status=429, setup=shown)

    def work():
        with _SETUP_LOCK:
            if _any_users():
                raise _WebRefused("already_set_up")
            real = (config.get().get("admin_token") or "").strip()
            if real:
                if not _token_ok(token.strip(), real):
                    raise _WebRefused("bad_token")
            elif setupcode.active():
                # A headless install: the one-time code printed by the installer stands in for being at the machine.
                if not setupcode.matches(token):
                    raise _WebRefused("bad_code")
            elif not _setup_local(request):
                raise _WebRefused("local_only")
            if password != confirm:
                raise _WebRefused("mismatch")
            user = users.create(username, name, "owner", password, temporary=False, actor=None)
            if api_key.strip():
                config.save({"openai_api_key": api_key.strip()[:300]})
            if real:
                # The shared token did its one job (it created the first Owner); accounts replace it.
                config.save({"admin_token": ""})
            # Setup signs the Owner in without a password check, so stamp the sign-in here.
            db.execute("update users set last_login = ? where id = ?", (db.now(), user.id))
            audit.record("setup.owner", "Set up Relay and made the first Owner", actor=user, target=user.username,
                         detail={"openai_key": True} if api_key.strip() else None, ip=ip)
            global _have_users
            _have_users = True
            setupcode.clear()
        ua = request.headers.get("user-agent", "")
        return (*users.start_session(user, via="password", user_agent=ua), _home_for(user))

    try:
        started = await asyncio.to_thread(work)
    except _WebRefused as exc:
        if exc.key in ("bad_token", "bad_code"):
            await asyncio.to_thread(audit.record, "setup.failed", "First-run setup refused: wrong "
                                    + ("admin token" if exc.key == "bad_token" else "setup code"), ip=ip)
            await _fail(ip)
        if exc.key == "local_only":
            return await asyncio.to_thread(_login_page, request, error=exc.message, status=403)
        if exc.key == "already_set_up":
            return await asyncio.to_thread(_login_page, request, error=exc.message, status=403)
        return await asyncio.to_thread(_login_page, request, error=exc.message,
                                       status=401 if exc.key in ("bad_token", "bad_code") else 400, setup=shown)
    except users.Refused as exc:
        return await asyncio.to_thread(_login_page, request, error=exc.message, status=_refusal_status(exc),
                                       setup=shown)
    _LOGIN_FAILS.pop(ip, None)
    return _signed_in(request, started)


@app.post("/admin/import")
async def admin_import(request: Request, folder: str = Form("")):
    """First run only, from this computer: bring in the settings of a v2.0 Docker install (app/importdocker.py).
    The old folder is read, never changed. Refused once any account exists."""
    def work():
        if _any_users():
            return _login_page(request, error=WEB_MESSAGES["already_set_up"], status=403)
        if not _setup_local(request):
            return _login_page(request, error=WEB_MESSAGES["local_only"], status=403)
        try:
            report = importdocker.import_from(_field(folder, 1000).strip().strip('"'), config.STATE_DIR)
        except importdocker.ImportRefused as exc:
            return _login_page(request, error=str(exc), status=400)
        except OSError:
            return _login_page(request, error="The old folder could not be read.", status=400)
        config.load()
        audit.record("setup.import", "Imported the settings of a Docker install", detail={"copied": report.copied},
                     ip=_client_ip(request))
        parts = ["Imported: " + ", ".join(report.copied or ["nothing new"]) + "."] + report.notes
        fp = tls.panel.describe().get("fingerprint")
        if fp:
            parts.append(f"New certificate fingerprint (SHA-256): {fp}")
        return _login_page(request, notice=" ".join(parts))
    return await asyncio.to_thread(work)


# The audit log's sentence for each refused sign-in, by users.Refused key.
_SIGNIN_REFUSED = {
    "bad_login": "Failed sign-in: wrong username or password",
    "disabled": "Sign-in refused: the account is turned off",
    "wrong_pin": "Failed sign-in: wrong PIN",
    "pin_cleared": "PIN cleared after too many wrong tries",
    "no_device": "PIN sign-in refused: not a trusted device",
}


def _audit_failed_signin(key: str, ip: str, who: User | None, way: str, device: str = "") -> None:
    """A refused sign-in, against the account it named when there is one. What was typed as a username is
    never written: it is sometimes a password typed in the wrong box."""
    detail = {"with": way, "reason": key}
    if device:
        detail["device"] = device
    audit.record("auth.failed", _SIGNIN_REFUSED.get(key, "Failed sign-in"),
                 target=who.username if who else "(unknown username)", detail=detail, ip=ip)


@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(""), password: str = Form("")):
    ip = _client_ip(request)
    username = _field(username, 100)
    left = _login_locked(ip)
    if left:
        log.warning("Admin login from %s refused, locked for %ds", ip, int(left))
        return await asyncio.to_thread(_login_page, request, error=_locked_sentence(left), status=429,
                                       username=username, pw=True)

    def work():
        _ready()
        try:
            user = users.authenticate(username, password)
        except users.Refused as exc:
            _audit_failed_signin(exc.key, ip, users.get_by_username(username), "password")
            raise
        audit.record("auth.password", "Signed in with their password", actor=user, ip=ip)
        ua = request.headers.get("user-agent", "")
        return (*users.start_session(user, via="password", user_agent=ua), _home_for(user))

    try:
        started = await asyncio.to_thread(work)
    except users.Refused as exc:
        if exc.key == "bad_login":
            await _fail(ip)
        return await asyncio.to_thread(_login_page, request, error=exc.message, status=_refusal_status(exc),
                                       username=username, pw=True)
    global _have_users
    _have_users = True
    _LOGIN_FAILS.pop(ip, None)
    return _signed_in(request, started)


@app.post("/admin/login/pin")
async def admin_login_pin(request: Request, user_id: str = Form(""), pin: str = Form("")):
    """A PIN signs in only from a browser an admin marked as a trusted device (the relay_device cookie)."""
    ip = _client_ip(request)
    uid = int(user_id) if user_id.isascii() and user_id.isdigit() and len(user_id) < 10 else -1
    left = _login_locked(ip)
    if left:
        return await asyncio.to_thread(_login_page, request, error=_locked_sentence(left), status=429,
                                       pin_user=uid)

    def work():
        _ready()
        device = users.device_for(request.cookies.get(DEVICE_COOKIE) or "")
        try:
            user = users.authenticate_pin(uid, _field(pin, 16), device["id"] if device else None)
        except users.Refused as exc:
            _audit_failed_signin(exc.key, ip, users.get(uid) if uid > 0 else None, "PIN",
                                 device["name"] if device else "")
            raise
        audit.record("auth.pin", "Signed in with their PIN", actor=user, detail={"device": device["name"]}, ip=ip)
        return (*users.start_session(user, via="pin", device_id=device["id"],
                                     user_agent=request.headers.get("user-agent", "")), _home_for(user))

    try:
        started = await asyncio.to_thread(work)
    except users.Refused as exc:
        if exc.key in ("wrong_pin", "pin_cleared"):
            await _fail(ip)
        # Without a trusted device there is no picker to go back to: the password form, with the sentence.
        return await asyncio.to_thread(_login_page, request, error=exc.message, status=_refusal_status(exc),
                                       pin_user=uid, pw=exc.key == "no_device")
    global _have_users
    _have_users = True
    _LOGIN_FAILS.pop(ip, None)
    return _signed_in(request, started)


@app.post("/admin/password")
async def admin_password(request: Request, current: str = Form(""), new: str = Form(""),
                         confirm: str = Form(""), voluntary: str = Form("")):
    """The signed-in user's own password: the form shown while a change is pending, and a plain change."""
    ip = _client_ip(request)
    left = _login_locked(ip)
    again = voluntary == "1"

    def work():
        user = _session_user(request)
        if user is None:
            return None
        if left:
            return _password_page(request, error=_locked_sentence(left), status=429, voluntary=again)
        if new != confirm:
            return _password_page(request, error=WEB_MESSAGES["mismatch"], status=400, voluntary=again)
        users.change_own_password(user, current, new, keep_token=_session_token(request))
        audit.record("account.password", "Changed their own password", actor=user, ip=ip)
        return _home_for(user)

    try:
        got = await asyncio.to_thread(work)
    except users.Refused as exc:
        if exc.key == "wrong_password":
            await _fail(ip)
        return await asyncio.to_thread(functools.partial(_password_page, request, error=exc.message,
                                                         status=_refusal_status(exc), voluntary=again))
    if got is None or isinstance(got, str):
        return RedirectResponse(got or "/admin", status_code=303)
    return got


@app.post("/admin/logout")
async def admin_logout(request: Request):
    token = _session_token(request)
    if token:
        ip = _client_ip(request)

        def work():
            user = users.session_user(token)
            users.end_session(token)
            if user is not None:
                audit.record("auth.signout", "Signed out", actor=user, ip=ip)

        # Ends it server-side, so a copied cookie is dead too. If the database is down the browser still
        # forgets its cookie.
        with contextlib.suppress(db.Unavailable):
            await asyncio.to_thread(work)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(ADMIN_COOKIE, path="/")
    return resp


RESCAN_LIVE_NOTE = (
    "Capture is running, so the audio backend was left alone \u2014 anything plugged in "
    "since start-up will not appear. Stop capture and rescan for a full list."
)


@app.get("/api/admin/urls", dependencies=[Depends(require("settings.view"))])
async def admin_urls():
    """Viewer links alone, so the admin page can follow a network change without a reload."""
    cfg = config.get()
    return {"urls": await asyncio.to_thread(_urls, serving.viewer_port(cfg["port"])),
            "serving": await asyncio.to_thread(_serving_info)}


@app.get("/api/admin/state", dependencies=[Depends(require("settings.view"))])
async def admin_state():
    from .audio import label_inputs, list_input_devices, selection_notice

    # Cached read: opening the panel must never re-initialise PortAudio, which
    # would close a live capture stream out from under the event.
    return {
        "config": config.redacted(),
        "status": engine.status(),
        "devices": label_inputs(list_input_devices()),
        "device_notice": selection_notice(config.get().get("audio_device")),
        "urls": _urls(serving.viewer_port(config.get()["port"])),
        "serving": _serving_info(),
        "timezones": [{"id": z, "label": label} for z, label in schedules.US_TIMEZONES],
    }


# --------------------------------------------------------------- modules (#157)
@app.get("/api/admin/modules", dependencies=[Depends(require("modules.view"))])
async def admin_modules():
    return {"modules": [module_registry.describe(m) for m in module_registry.all()]}


@app.post("/api/admin/modules/{mid}", dependencies=[Depends(require("modules.manage"))])
async def admin_module_switch(mid: str, payload: dict, request: Request):
    """Switch a module on or off. Its pages appear or go once the admin page reloads."""
    mod = module_registry.get(mid)
    if mod is None:
        raise HTTPException(404, "no such module")
    on = payload.get("enabled")
    if not isinstance(on, bool):
        return JSONResponse({"error": "enabled must be true or false"}, status_code=400)
    await module_registry.set_enabled(mid, on)
    audit.note(request, "module.switch", f"Switched the {mod.manifest.name} module {'on' if on else 'off'}",
               target=mid)
    return module_registry.describe(module_registry.get(mid))


@app.post("/api/admin/modules/{mid}/test", dependencies=[Depends(require("modules.manage"))])
async def admin_module_test(mid: str):
    mod = module_registry.get(mid)
    if mod is None:
        raise HTTPException(404, "no such module")
    if not module_registry.enabled(mid):
        return JSONResponse({"ok": False, "detail": "Switch the module on first."}, status_code=409)
    try:
        result = await mod.test()
    except Exception as exc:
        log.exception("module %s test failed", mid)
        result = {"ok": False, "detail": f"The test failed: {type(exc).__name__}"}
    return {"ok": bool(result.get("ok")), "detail": str(result.get("detail") or "")}


# --------------------------------------------------------------- network ports (#347)
def _browsing_from(request: Request) -> str | None:
    """The address of this machine the browser connected to (which network the admin is using)."""
    server = request.scope.get("server")
    return str(server[0]) if server and server[0] else None


@app.get("/api/admin/network", dependencies=[Depends(require("network.view"))])
async def admin_network(request: Request):
    """Every network port Relay has seen, every kind of traffic with its route and status, and warnings."""
    out = await asyncio.to_thread(netroute.overview)
    out["browsing_from"] = _browsing_from(request)
    return out


@app.get("/api/admin/network/map", dependencies=[Depends(require("network.view"))])
async def admin_network_map(refresh: int = 0):
    """The network map (#437): every device Relay can see, merged from its own ports, mDNS discovery of NDI and Dante,
    and every module that is on and adds to the map. Core imports no module; see app/network_map.py."""
    return await network_map.snapshot(module_registry, force=bool(refresh))


@app.post("/api/admin/network/map/labels", dependencies=[Depends(require("network.manage"))])
async def admin_network_labels(payload: dict, request: Request):
    """Set or clear the names Relay shows for devices and the notes on devices and switch ports. A name changes only
    what Relay shows; nothing is sent to the device. {"names": {id: text}, "notes": {id or "switch#port": text}}."""
    names, notes = payload.get("names"), payload.get("notes")
    if names is not None and not isinstance(names, dict) or notes is not None and not isinstance(notes, dict):
        return JSONResponse({"error": "names and notes must be objects"}, status_code=400)
    saved = await asyncio.to_thread(network_map.save_labels, names, notes)
    audit.note(request, "network.labels", f"Changed {len(names or {})} device names and {len(notes or {})} notes on the network map",
               detail={"names": sorted((names or {}).keys())[:20], "notes": sorted((notes or {}).keys())[:20]})
    return saved


@app.post("/api/admin/network/map/places", dependencies=[Depends(require("network.manage"))])
async def admin_network_places(payload: dict, request: Request):
    """Replace the closets and racks, each with its switches top to bottom: {"places": [{"name", "gear": [id]}]}."""
    if not isinstance(payload.get("places"), list):
        return JSONResponse({"error": "places must be a list"}, status_code=400)
    saved = await asyncio.to_thread(network_map.save_places, payload["places"])
    audit.note(request, "network.places", f"Set {len(saved['places'])} places for the network gear",
               detail={"places": [p["name"] for p in saved["places"]]})
    return saved


@app.get("/api/admin/network/cables", dependencies=[Depends(require("network.view"))])
async def admin_network_cables():
    """Every cable between two ports, and the gear with its ports to pick from. A cable is core (app/cables.py): the
    Network page and a module's page that records one show the same list."""
    def read():
        return {"cables": network_map.cable_rows(), "gear": network_map.cabling_gear()}
    try:
        return await asyncio.to_thread(read)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so cables cannot be read right now."}, status_code=503)


@app.post("/api/admin/network/cables", dependencies=[Depends(require("network.manage"))])
async def admin_network_cable_add(payload: dict, request: Request):
    """Record a cable between two ports: {"a": "port:relay:1", "b": "port:relay:2", "note": ""}."""
    try:
        made = await asyncio.to_thread(network_map.add_cable, payload.get("a"), payload.get("b"), payload.get("note", ""))
    except cables.Refused as exc:
        return JSONResponse({"error": str(exc)}, status_code=409 if exc.key == "exists" else 400)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so cables cannot be saved right now."}, status_code=503)
    audit.note(request, "network.cable_add", "Recorded a cable between two ports",
               detail={"a": made["a"]["ref"], "b": made["b"]["ref"]})
    return {"cable": made["ref"], "cables": network_map.cable_rows()}


@app.post("/api/admin/network/cables/{cid}/note", dependencies=[Depends(require("network.manage"))])
async def admin_network_cable_note(cid: int, payload: dict, request: Request):
    """The note on a cable, one an operator recorded or one a module reports. Empty text clears it."""
    text = payload.get("text", "")
    if not isinstance(text, str) or len(text) > cables.NOTE_MAX:
        return JSONResponse({"error": f"A note is up to {cables.NOTE_MAX} characters."}, status_code=400)
    try:
        await asyncio.to_thread(cables.set_note, cid, text)
    except cables.Refused as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so notes cannot be saved right now."}, status_code=503)
    audit.note(request, "network.cable_note", "Changed a note on a cable", target=cables.ref(cid))
    return {"cables": network_map.cable_rows()}


@app.delete("/api/admin/network/cables/{cid}", dependencies=[Depends(require("network.manage"))])
async def admin_network_cable_remove(cid: int, request: Request):
    """Remove a cable an operator recorded. A cable a module reports (UniFi's uplinks) cannot be removed here."""
    try:
        await asyncio.to_thread(cables.remove, cid)
    except cables.Refused as exc:
        return JSONResponse({"error": str(exc)}, status_code=404 if exc.key == "no_cable" else 409)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so cables cannot be removed right now."}, status_code=503)
    audit.note(request, "network.cable_remove", "Removed a cable between two ports", target=cables.ref(cid))
    return {"cables": network_map.cable_rows()}


def _channels_view() -> dict:
    """Every channel with its ends, and the pairs Relay suggests are one channel (never joined on its own)."""
    def slim(c: dict) -> dict:
        return {"id": c["id"], "ref": c["ref"], "shown": c["shown"], "kind": c["kind"], "providers": c["providers"]}
    chans, routes = channels.listing(), routing.listing()
    by_dest = {v["destination"]: v for v in routes}
    for c in chans:        # what feeds each channel and where it goes (app/routing.py), the same on every page
        v = by_dest.get(c["ref"])
        c["fed_by"] = [s["name"] for s in v["sources"]] if v else []
        c["asked"] = [s["name"] for s in v["wanted"]] if v and v["state"] == "differs" else []
        c["feeds"] = [r["name"] for r in routes if any(s["ref"] == c["ref"] for s in r["sources"])]
    return {"channels": chans,
            "suggestions": [{"a": slim(x["a"]), "b": slim(x["b"]), "why": x["why"]} for x in channels.suggestions()]}


async def _channel_change(fn, *args):
    """Run one channel change off the event loop and answer with the whole view, or a fixed sentence for a refusal."""
    try:
        await asyncio.to_thread(fn, *args)
        return await asyncio.to_thread(_channels_view)
    except channels.Refused as exc:
        return JSONResponse({"error": channels.REFUSALS[exc.key]}, status_code=409 if exc.key in ("kind", "present", "same", "device", "no_device") else 404)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so channels cannot be changed right now."}, status_code=503)


@app.get("/api/admin/network/channels", dependencies=[Depends(require("network.view"))])
async def admin_network_channels():
    """Every audio, video and talk channel, however many modules report it, and the joins Relay suggests."""
    try:
        return await asyncio.to_thread(_channels_view)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so channels cannot be read right now."}, status_code=503)


@app.post("/api/admin/network/channels/merge", dependencies=[Depends(require("network.manage"))])
async def admin_network_channel_merge(payload: dict, request: Request):
    """Join two channels an operator says are one: {"keep": "channel:relay:1", "other": "channel:relay:2"}."""
    keep, other = channels.channel_id(payload.get("keep")), channels.channel_id(payload.get("other"))
    if keep is None or other is None:
        return JSONResponse({"error": channels.REFUSALS["no_channel"]}, status_code=400)
    out = await _channel_change(channels.merge, keep, other)
    audit.note(request, "network.channel_merge", "Joined two channels into one", detail={"keep": channels.ref(keep), "other": channels.ref(other)})
    return out


@app.post("/api/admin/network/channels/split", dependencies=[Depends(require("network.manage"))])
async def admin_network_channel_split(payload: dict, request: Request):
    """Take one end out of a channel: {"end": 12}."""
    end = payload.get("end")
    if not isinstance(end, int) or isinstance(end, bool):
        return JSONResponse({"error": channels.REFUSALS["no_end"]}, status_code=400)
    out = await _channel_change(channels.split, end)
    audit.note(request, "network.channel_split", "Split a channel apart", detail={"end": end})
    return out


@app.post("/api/admin/network/channels/decline", dependencies=[Depends(require("network.manage"))])
async def admin_network_channel_decline(payload: dict, request: Request):
    """Say two channels are not the same, so they are not suggested again: {"a": "channel:relay:1", "b": "channel:relay:2"}."""
    a, b = channels.channel_id(payload.get("a")), channels.channel_id(payload.get("b"))
    if a is None or b is None:
        return JSONResponse({"error": channels.REFUSALS["no_channel"]}, status_code=400)
    out = await _channel_change(channels.decline, a, b)
    audit.note(request, "network.channel_decline", "Said two channels are not the same", detail={"a": channels.ref(a), "b": channels.ref(b)})
    return out


@app.post("/api/admin/network/channels/{cid}/rename", dependencies=[Depends(require("network.manage"))])
async def admin_network_channel_rename(cid: int, payload: dict, request: Request):
    """Rename a channel. {"name": "...", "device": false}: a Relay-only name (empty shows the gear's own name again), or
    with "device": true the new name is sent to the gear and shown from there (#543)."""
    name = payload.get("name", "")
    if not isinstance(name, str) or len(name) > channels.NAME_MAX:
        return JSONResponse({"error": f"A name is up to {channels.NAME_MAX} characters."}, status_code=400)
    if payload.get("device") is True:
        out = await _channel_change(channels.rename_on_device, cid, name)
        if isinstance(out, JSONResponse):
            return out
        audit.note(request, "network.channel_rename_device", "Renamed a channel on the gear", target=channels.ref(cid),
                   detail={"name": name})
        return out
    out = await _channel_change(channels.rename, cid, name)
    if isinstance(out, JSONResponse):
        return out
    audit.note(request, "network.channel_rename", "Renamed a channel in Relay only", target=channels.ref(cid),
               detail={"name": name})
    return out


@app.post("/api/admin/network/ports/relay/{pid}/rename", dependencies=[Depends(require("network.manage"))])
async def admin_network_port_rename(pid: int, payload: dict, request: Request):
    """A Relay-only name for a core port; the gear's own name is kept and returned beside it. Empty text clears it."""
    name = payload.get("name", "")
    if not isinstance(name, str) or len(name) > ports.NAME_MAX:
        return JSONResponse({"error": f"A name is up to {ports.NAME_MAX} characters."}, status_code=400)
    try:
        await asyncio.to_thread(ports.rename, pid, name)
        row = await asyncio.to_thread(ports.get, pid)
    except ports.Refused as exc:
        return JSONResponse({"error": ports.REFUSALS[exc.key]}, status_code=404)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so ports cannot be changed right now."}, status_code=503)
    audit.note(request, "network.port_rename", "Renamed a port in Relay only", target=ports.ref(pid), detail={"name": name})
    return {"port": row, "device_updated": False}


@app.post("/api/admin/network/channels/{cid}/note", dependencies=[Depends(require("network.manage"))])
async def admin_network_channel_note(cid: int, payload: dict, request: Request):
    """The note on a channel. Empty text clears it."""
    text = payload.get("text", "")
    if not isinstance(text, str) or len(text) > channels.NOTE_MAX:
        return JSONResponse({"error": f"A note is up to {channels.NOTE_MAX} characters."}, status_code=400)
    out = await _channel_change(channels.set_note, cid, text)
    audit.note(request, "network.channel_note", "Changed a note on a channel", target=channels.ref(cid))
    return out


@app.delete("/api/admin/network/channels/{cid}", dependencies=[Depends(require("network.manage"))])
async def admin_network_channel_forget(cid: int, request: Request):
    """Forget a channel that no module reports any more."""
    out = await _channel_change(channels.forget, cid)
    audit.note(request, "network.channel_forget", "Forgot a channel", target=channels.ref(cid))
    return out


@app.post("/api/admin/network/classes/{cid}", dependencies=[Depends(require("network.manage"))])
async def admin_network_route(cid: str, payload: dict, request: Request):
    """Put a kind of traffic on a network port (port_id null: the system default) and set its policy."""
    port_id = payload.get("port_id")
    policy = payload.get("policy")
    if port_id is not None and not isinstance(port_id, str) or policy is not None and not isinstance(policy, str):
        return JSONResponse({"error": "port_id and policy must be text or null"}, status_code=400)
    try:
        r = await asyncio.to_thread(netroute.change, cid, port_id, policy, _browsing_from(request))
    except netroute.RouteRefused as exc:
        return JSONResponse({"error": exc.message}, status_code=409)
    label = netroute.classes()[cid][1].label
    where = (r.port.friendly_name or r.port.name) if r.port else ("the chosen network port" if r.routed else
                                                                  "the system's default network")
    audit.note(request, "network.route", f"Put {label} on {where} ({r.policy})", target=cid,
               detail={"port_id": r.port_id, "policy": r.policy})
    return await asyncio.to_thread(netroute.overview)


@app.post("/api/admin/network/classes/{cid}/test", dependencies=[Depends(require("network.manage"))])
async def admin_network_test(cid: str):
    """Where this traffic would leave from now, and the owning module's own connection test when it has one."""
    from . import netsock

    cls = netroute.classes()
    if cid not in cls:
        raise HTTPException(404, "no such traffic")
    out = await asyncio.to_thread(netsock.probe, cid)
    owner = cls[cid][0]
    mod = module_registry.get(owner) if owner else None
    if mod is not None and module_registry.enabled(owner) and type(mod).test is not Module.test:
        try:
            got = await mod.test()
            out["module"] = {"ok": bool(got.get("ok")), "detail": str(got.get("detail") or "")}
        except Exception as exc:
            log.exception("module %s test failed", owner)
            out["module"] = {"ok": False, "detail": f"The test failed: {type(exc).__name__}"}
    return out


@app.post("/api/admin/network/ports/{pid}/forget", dependencies=[Depends(require("network.manage"))])
async def admin_network_forget(pid: str, request: Request):
    """Forget a network port that is gone and carries no traffic."""
    try:
        await asyncio.to_thread(netroute.forget, pid)
    except netroute.RouteRefused as exc:
        return JSONResponse({"error": exc.message}, status_code=409)
    audit.note(request, "network.forget", "Forgot a network port", target=pid)
    return await asyncio.to_thread(netroute.overview)


@app.get("/api/admin/devices", dependencies=[Depends(require("session.view"))])
async def admin_devices():
    from .audio import capture_is_live, label_inputs, list_input_devices, selection_notice

    live = capture_is_live()
    return {
        "devices": label_inputs(list_input_devices(refresh=True)),
        "device_notice": selection_notice(config.get().get("audio_device")),
        "full_rescan": not live,
        "note": RESCAN_LIVE_NOTE if live else None,
    }


# -- routes (#544): which source channel feeds which destination channel -------------------------------------------
async def _routes_do(fn, *args, **kw):
    """Run one routing call off the event loop: its result, or a fixed sentence for a refusal."""
    try:
        return await asyncio.to_thread(fn, *args, **kw)
    except routing.Refused as exc:
        return JSONResponse({"error": routing.REFUSALS[exc.key]}, status_code=404 if exc.key in ("no_channel", "no_route", "no_salvo") else 409)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so routes cannot be changed right now."}, status_code=503)


def _routes_view(provider: str = "", feed: str = "") -> dict:
    out = {"routes": routing.listing(provider), "salvos": routing.salvos()}
    if feed:
        out["feeds"] = routing.feeds(feed)
        out["feeding"] = routing.feeding(feed)
    return out


@app.get("/api/admin/routes", dependencies=[Depends(require("network.view"))])
async def admin_routes(provider: str = "", channel: str = ""):
    """Every route (what was asked for and what the gear does, with any difference) and every preset. With `channel`
    (a channel ref): "what feeds this?" and "where does this go?" for it."""
    return await _routes_do(_routes_view, provider[:32], channel[:60])


async def _may_route(request: Request, destination) -> JSONResponse | None:
    """403 unless the user holds network.manage or the permission the destination's module names (routing.may_route)."""
    user = request.state.user
    try:
        if await asyncio.to_thread(routing.may_route, destination, user.can):
            return None
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so routes cannot be changed right now."}, status_code=503)
    return JSONResponse({"error": WEB_MESSAGES["not_allowed"]}, status_code=403)


@app.post("/api/admin/routes", dependencies=[Depends(require(None))])
async def admin_routes_set(payload: dict, request: Request):
    """{"destination": "channel:relay:3", "source": "channel:relay:7", "gain_db": -3}: the destination takes the source.
    Needs network.manage, or the permission the destination's module names (an NDI output: ndi.manage)."""
    if (no := await _may_route(request, payload.get("destination"))) is not None:
        return no
    got = await _routes_do(routing.set, payload.get("destination"), payload.get("source"), payload.get("gain_db"))
    if isinstance(got, JSONResponse):
        return got
    audit.note(request, "routes.set", "Routed a source to a destination",
               detail={"destination": payload.get("destination"), "source": payload.get("source")})
    return {"status": got["status"], "problems": got["problems"]}


@app.post("/api/admin/routes/clear", dependencies=[Depends(require(None))])
async def admin_routes_clear(payload: dict, request: Request):
    """{"destination": ref, "source": ref}: take the destination off that source, or off everything without `source`.
    The same permission as setting a route."""
    if (no := await _may_route(request, payload.get("destination"))) is not None:
        return no
    got = await _routes_do(routing.clear, payload.get("destination"), payload.get("source"))
    if isinstance(got, JSONResponse):
        return got
    audit.note(request, "routes.clear", "Took a destination off a source", detail={"destination": payload.get("destination")})
    return {"status": got["status"], "problems": got["problems"]}


@app.post("/api/admin/routes/salvos", dependencies=[Depends(require("network.manage"))])
async def admin_routes_salvo_save(payload: dict, request: Request):
    """{"name", "destinations": [refs], "scope", "id"}: save what those destinations take now as a preset."""
    dests, sid = payload.get("destinations"), payload.get("id")
    if not isinstance(dests, list) or len(dests) > 500:
        return JSONResponse({"error": "Choose the destinations to save."}, status_code=400)
    got = await _routes_do(routing.save_salvo, payload.get("name"), dests, payload.get("scope") or "",
                           sid if isinstance(sid, int) and not isinstance(sid, bool) else None)
    if isinstance(got, JSONResponse):
        return got
    audit.note(request, "routes.salvo_save", "Saved a route preset", target=got["name"])
    return got


@app.post("/api/admin/routes/salvos/{sid}/take", dependencies=[Depends(require("network.manage"))])
async def admin_routes_salvo_take(sid: int, request: Request):
    got = await _routes_do(routing.take_salvo, sid)
    if isinstance(got, JSONResponse):
        return got
    audit.note(request, "routes.salvo_take", "Took a route preset", target=str(sid))
    return {"problems": got}


@app.delete("/api/admin/routes/salvos/{sid}", dependencies=[Depends(require("network.manage"))])
async def admin_routes_salvo_delete(sid: int, request: Request):
    got = await _routes_do(routing.delete_salvo, sid)
    if isinstance(got, JSONResponse):
        return got
    audit.note(request, "routes.salvo_delete", "Deleted a route preset", target=str(sid))
    return {"ok": True}


@app.get("/api/admin/audiobus", dependencies=[Depends(require("session.view"))])
async def admin_audiobus():
    """The 32-lane audio bus (app/audiobus.py): its saved setting, the live state of every lane, input and
    output, and what can be patched (devices and registered sources and outputs)."""
    from . import audio as audiomod

    return {
        "config": {**config.get()["audio_bus"], "matrix": await asyncio.to_thread(audiobus.matrix_from_routes)},
        "status": audiobus.bus.status(),
        "lanes": audiobus.LANES,
        "sources": [{"id": s.id, "label": s.label} for s in audiomod.sources()],
        "outputs": [{"id": o.id, "label": o.label} for o in audiomod.outputs()],
        "input_devices": audiomod.list_input_devices(),
        "output_devices": await asyncio.to_thread(audiomod.list_output_devices),
    }


@app.post("/api/admin/audiobus", dependencies=[Depends(require("audio.manage"))])
async def admin_audiobus_save(request: Request, payload: dict):
    """Save the bus setting and apply it. Only what changed is touched: a device or output that is the same as
    before keeps running."""
    cfg = audiobus.normalize(payload)
    cells = cfg["matrix"]
    # The crosspoints are core routes (app/routing.py): config keeps the lanes and outputs, the routes keep who feeds what.
    # Without "matrix" (the Audio bus page, whose crosspoints are the shared route editor) the routes are left alone.
    saved = config.save({"audio_bus": {**cfg, "matrix": []}}, replace=("audio_bus",))["audio_bus"]
    try:
        if "matrix" in payload:
            await asyncio.to_thread(audiobus.save_matrix, saved, cells)
        else:
            await asyncio.to_thread(audiobus.report_channels, saved)
    except db.Unavailable:
        return JSONResponse({"error": "The database is down, so the crosspoints cannot be saved right now."}, status_code=503)
    cfg = {**saved, "matrix": await asyncio.to_thread(audiobus.matrix_from_routes)}
    await asyncio.to_thread(audiobus.bus.apply, cfg)
    audit.note(request, "audio.bus", "Changed the audio bus", detail={
        "enabled": cfg["enabled"], "lanes": len(cfg["lanes"]), "outputs": len(cfg["outputs"]),
        "crosspoints": len(cfg["matrix"])})
    return {"config": cfg, "status": audiobus.bus.status()}


@app.get("/api/admin/metering", dependencies=[Depends(require("session.view"))])
async def admin_metering():
    """The sound meter (app/metering.py): its saved setting and the live reading."""
    return {"config": config.get()["metering"], "status": metering.meter.snapshot(), "lanes": audiobus.LANES}


@app.post("/api/admin/metering", dependencies=[Depends(require("audio.manage"))])
async def admin_metering_save(request: Request, payload: dict):
    """Save the meter's setting and apply it. What the body leaves out is kept; setting a different calibration by
    hand (against a reference meter) counts as calibrated."""
    cur = config.get()["metering"]
    body = metering.normalize({**cur, **payload})
    if "calibration_db" in payload and body["calibration_db"] != cur["calibration_db"] and "calibrated" not in payload:
        body["calibrated"] = True
    cfg = config.save({"metering": body}, replace=("metering",))["metering"]
    await asyncio.to_thread(metering.meter.apply, cfg)
    audit.note(request, "audio.metering", "Changed the sound meter", detail={
        "enabled": cfg["enabled"], "lane": cfg["lane"], "kind": cfg["kind"]})
    return {"config": cfg, "status": metering.meter.snapshot()}


@app.post("/api/admin/metering/calibrate", dependencies=[Depends(require("audio.manage"))])
async def admin_metering_calibrate(request: Request, payload: dict):
    """Set the calibration from a reference tone: body {"reference_db": 94}. Answers a sentence when there is no
    signal to read."""
    try:
        cal = await asyncio.to_thread(metering.meter.calibrate, payload.get("reference_db"))
    except metering.CalibrateError as exc:
        return JSONResponse({"error": metering.CALIBRATE_SENTENCES[exc.reason]}, status_code=409)
    cfg = config.save({"metering": {**config.get()["metering"], "calibration_db": cal, "calibrated": True}},
                      replace=("metering",))["metering"]
    audit.note(request, "audio.metering", "Calibrated the sound meter", detail={"calibration_db": cal})
    return {"config": cfg, "status": metering.meter.snapshot()}


@app.get("/api/admin/mixreport", dependencies=[Depends(require("session.view"))])
async def admin_mixreport():
    """The stream-mix report (app/mixreport.py): its setting, what it is doing now and the saved sessions."""
    return {"config": config.get()["mixreport"], "status": mixreport.recorder.status(),
            "sessions": await asyncio.to_thread(mixreport.list_sessions), "lanes": audiobus.LANES}


@app.post("/api/admin/mixreport", dependencies=[Depends(require("audio.manage"))])
async def admin_mixreport_save(request: Request, payload: dict):
    """Save the report's setting and apply it. What the body leaves out is kept."""
    body = mixreport.normalize({**config.get()["mixreport"], **payload})
    cfg = config.save({"mixreport": body}, replace=("mixreport",))["mixreport"]
    await asyncio.to_thread(mixreport.recorder.apply, cfg)
    audit.note(request, "audio.mixreport", "Changed the stream-mix report", detail={
        "enabled": cfg["enabled"], "left": cfg["left"], "right": cfg["right"], "rehearsal": cfg["rehearsal"]})
    return {"config": cfg, "status": mixreport.recorder.status()}


@app.get("/api/admin/mixreport/sessions/{sid}", dependencies=[Depends(require("session.view"))])
async def admin_mixreport_session(sid: str, rehearsal: int = 0):
    """One saved session, judged against the report's targets. Rehearsal seconds are left out unless asked for."""
    got = await asyncio.to_thread(mixreport.read_session, sid)
    if got is None:
        return JSONResponse({"error": "That report is not there."}, status_code=404)
    head, rows = got
    kept = [r for r in rows if rehearsal or not r.get("r")]
    return {"id": sid, "started": head.get("started"), "label": head.get("label", ""),
            "rehearsal_seconds": len(rows) - len([r for r in rows if not r.get("r")]),
            "report": mixreport.judge(kept, config.get()["mixreport"]), "series": mixreport.series(kept)}


@app.get("/api/admin/mixreport/sessions/{sid}/csv", dependencies=[Depends(require("session.view"))])
async def admin_mixreport_csv(sid: str):
    got = await asyncio.to_thread(mixreport.read_session, sid)
    if got is None:
        return JSONResponse({"error": "That report is not there."}, status_code=404)
    return Response(mixreport.csv_text(got[1]), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="stream-mix-{sid}.csv"'})


@app.get("/api/admin/status", dependencies=[Depends(require("session.view"))])
async def admin_status():
    return engine.status()


@app.get("/api/admin/status/stream", dependencies=[Depends(require("session.view"))])
async def admin_status_stream(request: Request):
    q = hub.subscribe_status()

    async def gen():
        # The full status object carries the whole blocklist (~5 KB). Push it
        # once up front and thereafter only on a real change (via push_status);
        # the 1 Hz tick that keeps the meter live sends a <200-byte meter frame.
        try:
            yield _sse(engine.status())
            yield _sse(_panels_frame())
            yield _sse(_alerts_frame())
            online = panel_registry.online_signature()
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    msg = engine.meter()
                yield _sse(msg)
                # A panel that stopped checking in sends nothing to announce it.
                now_online = panel_registry.online_signature()
                if now_online != online:
                    online = now_online
                    yield _sse(_panels_frame())
        finally:
            hub.unsubscribe_status(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# What each field of POST /api/admin/config needs. A field that is not here is not applied.
CONFIG_PERMISSIONS = {
    "audio_device": "audio.manage",
    "audio_source": "audio.manage",
    "input_channel": "audio.manage",
    "source_language": "audio.manage",
    "default_font_px": "settings.manage",
    "speaking_indicator_vad": "settings.manage",
    "history_lines": "settings.manage",
    "timezone": "settings.manage",
    "realtime": "settings.manage",
    "ai": "settings.manage",
    "openai_api_key": "credentials.manage",
    "vercel_api_key": "credentials.manage",
    "backup_passphrase": "credentials.manage",
    "recording": "recordings.manage",
    "blocklist": "blocklist.manage",
}


@app.post("/api/admin/config", dependencies=[Depends(require("settings.view"))])
async def admin_config(request: Request, payload: dict):
    # Every supplied field is checked before any is applied: one that the role may not change refuses the
    # whole request, so nothing is half saved.
    user: User = request.state.user
    if any(f in payload and not user.can(perm) for f, perm in CONFIG_PERMISSIONS.items()):
        raise HTTPException(status_code=403, detail=WEB_MESSAGES["not_allowed"])

    updates: dict = {}
    allowed_scalars = (
        "audio_device",
        "audio_source",
        "input_channel",
        "source_language",
        "default_font_px",
        "speaking_indicator_vad",
        "history_lines",
    )
    for field in allowed_scalars:
        if field in payload:
            updates[field] = payload[field]

    if "timezone" in payload:
        # The one Relay time zone: every schedule, signage slot, board row and call time reads it.
        if payload["timezone"] not in schedules.TZ_LABELS:
            raise HTTPException(status_code=400, detail=schedules.MESSAGES["zone"])
        updates["timezone"] = payload["timezone"]

    key = payload.get("openai_api_key")
    if isinstance(key, str) and key.strip():
        updates["openai_api_key"] = key.strip()
    vkey = payload.get("vercel_api_key")
    if isinstance(vkey, str) and vkey.strip():
        updates["vercel_api_key"] = vkey.strip()

    if "backup_passphrase" in payload:
        # Only the key derived from it is kept (app/backup.py); null or "" removes it.
        phrase = payload["backup_passphrase"]
        try:
            updates["backup_key"] = backupmod.passphrase_setting(phrase if isinstance(phrase, str) else "")
        except ValueError:
            raise HTTPException(status_code=400, detail=WEB_MESSAGES["backup_passphrase_short"]) from None

    if isinstance(payload.get("realtime"), dict):
        updates["realtime"] = payload["realtime"]

    if isinstance(payload.get("ai"), dict):
        # Which provider and model each AI task uses (app/aimodels.py normalises what is sent).
        updates["ai"] = {"tasks": payload["ai"].get("tasks")} if isinstance(payload["ai"].get("tasks"), dict) else {}

    if isinstance(payload.get("recording"), dict):
        updates["recording"] = payload["recording"]

    cfg = config.save(updates)
    hub.history_lines = cfg["history_lines"]
    if "timezone" in updates:
        await scheduler.poke()
    # The names of the fields changed, never their values (the OpenAI key is one of them).
    changed = sorted(f for f in CONFIG_PERMISSIONS if f in payload)
    audit.note(request, "settings.change", "Changed settings: " + (", ".join(changed) or "nothing"),
               detail={"fields": changed})
    # Takes effect on the next start: an event already being recorded keeps
    # writing where it began, rather than losing its file mid-run.
    recorder.configure(config.recordings_path(), cfg["recording"]["keep_runs"])

    blocklist_report = None
    if "blocklist" in payload:
        # The file is the source of truth; the panel is one way to edit it.
        report = redact.parse_terms_report(payload["blocklist"])
        terms = redact.write_blocklist_file(config.blocklist_path(), report.terms)
        hub.set_blocklist(terms)
        # Collapsed or cut-off entries vanish from the textarea; say so.
        blocklist_report = {
            "terms": len(terms),
            "duplicates": report.duplicates,
            "dropped": report.dropped,
            "max_terms": redact.MAX_TERMS,
        }

    # Changes that alter the audio path or model behaviour need a session cycle.
    needs_restart = any(
        f in updates
        for f in ("audio_device", "audio_source", "input_channel", "source_language", "realtime")
    )
    if needs_restart and engine.running:
        await engine.restart()
    elif engine.running and any(f in updates for f in ("ai", "openai_api_key", "vercel_api_key")):
        # A translation provider or key change moves the translator only; captions keep running.
        await engine.apply_ai()

    body = {"config": config.redacted(), "status": engine.status()}
    if blocklist_report is not None:
        body["blocklist_report"] = blocklist_report
    return JSONResponse(body)


@app.get("/api/admin/ai", dependencies=[Depends(require("settings.manage"))])
async def admin_ai():
    """The AI models page: each task's provider and model and what it runs on now, the transcription backends,
    and which keys are saved."""
    cfg = config.get()
    rt = cfg["realtime"]
    backend, warning = transcribe.resolve(cfg)
    return {
        "tasks": aimodels.routes(cfg),
        "transcription": {
            "backend": rt.get("backend") or transcribe.DEFAULT_BACKEND,
            "scribe_model": rt.get("scribe_model"),
            "openai_model": rt.get("transcribe_model") or config.DEFAULTS["realtime"]["transcribe_model"],
            "running": backend.id, "warning": warning,
            "backends": [{"id": b.id, "label": b.label, "available": b.available(cfg),
                          "price_per_hour": b.price_per_hour} for b in transcribe.backends()],
        },
        "keys": {"vercel": bool((cfg.get("vercel_api_key") or "").strip()),
                 "openai": bool((cfg.get("openai_api_key") or "").strip())},
    }


@app.get("/api/admin/ai/models", dependencies=[Depends(require("settings.manage"))])
async def admin_ai_models(refresh: bool = False):
    """Vercel's model list for the pickers, with each model's price and whether it is free (cached for an hour)."""
    return await asyncio.to_thread(aimodels.catalogue.get, refresh)


@app.post("/api/admin/target/{target}", dependencies=[Depends(require("targets.manage"))])
async def admin_target(target: str, payload: dict):
    target = target.upper()
    try:
        status = await engine.set_target(
            target, bool(payload.get("enabled"))
        )
    except KeyError:
        raise HTTPException(404, "unknown target")
    return status


@app.post("/api/admin/start", dependencies=[Depends(require("session.control"))])
async def admin_start():
    scheduler.manual_start()
    try:
        return await engine.start()
    except RuntimeError as exc:
        # The refusal last: status() carries its own "error" (the sticky
        # last_error), which must not mask why this press failed.
        # A fixed sentence by key (or the capture's own, kept in the status), never the exception's own text.
        error = engine_mod.START_MESSAGES.get(getattr(exc, "key", "")) or engine.last_error \
            or "Captioning could not start."
        return JSONResponse({**engine.status(), "error": error}, status_code=400)


@app.post("/api/admin/stop", dependencies=[Depends(require("session.control"))])
async def admin_stop():
    # Before the stop, so the status it pushes already shows the window held.
    scheduler.manual_stop()
    return await engine.stop()


# ---------------------------------------------------------------- schedules
# Stored in config.json under `schedules`; see app/schedules.py for the shape
# and app/scheduler.py for what starts and stops when.

# ---------------------------------------------------------------- panels
def _firmware_boards(rows: list[dict] | None = None) -> list[dict]:
    """One row per board that is not on hold: its image (or None), and how many of its panels are online
    and how many of those run something older than the image."""
    rows = panel_registry.listing() if rows is None else rows
    out = []
    for board in panelfw.active_boards():
        info = panelfw.current(board.id)
        mine = [r for r in rows if r["board"] == board.id and r["online"]]
        out.append({"board": board.id, "label": board.label,
                    "firmware": info.public() if info else None,
                    "online": len(mine),
                    "outdated": sum(1 for r in mine if r["update_available"])})
    return out


def _panels_frame() -> dict:
    rows = panel_registry.listing()
    boards = _firmware_boards(rows)
    return {"type": "panels", "panels": rows, "limit": panels.MAX_PANELS,
            "offline_after_s": panels.OFFLINE_AFTER_S,
            "boards": boards,
            # The CrowPanel's image, as before #122, for a page that reads one image.
            "firmware": next((b["firmware"] for b in boards if b["board"] == panelfw.DEFAULT_BOARD), None)}


def _board_arg(board: str) -> str:
    """A board named by the admin page, or the CrowPanel when it names none. 400 for one Relay does not
    know or that is on hold."""
    board = board or panelfw.DEFAULT_BOARD
    b = panelfw.BOARDS.get(board)
    if b is None:
        raise HTTPException(400, "unknown board")
    if b.on_hold:
        raise HTTPException(400, f"the {b.label} is on hold; Relay keeps no firmware for it")
    return board


def _alerts_frame() -> dict:
    return {"type": "alerts_admin", **alerts.admin_view()}


alerts.on_change = lambda: hub.push_status(_alerts_frame())


@app.get("/api/admin/alerts", dependencies=[Depends(require("alerts.view"))])
async def admin_alerts():
    return _alerts_frame()


@app.post("/api/admin/alerts", dependencies=[Depends(require("alerts.manage"))])
async def admin_alerts_save(payload: dict):
    """The alert buttons and the auto-stop, whole: {"auto_stop_s", "buttons": [{"id"?, "name", "panels",
    "screens"}]}. A button left out is deleted, and an alert it had flashing stops."""
    try:
        alerts.save(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _alerts_frame()


@app.post("/api/admin/alerts/test", dependencies=[Depends(require("alerts.manage"))])
async def admin_alerts_test(payload: dict):
    """Show "Test from Relay" on one panel ({"panel": id}) or browser screen ({"screen": name}) for a message's
    time. Answers how many of its streams took it; 0 means it is not connected."""
    try:
        took = alerts.test(panel=payload.get("panel"), screen=payload.get("screen"))
    except alertsmod.Refused as exc:
        raise HTTPException(exc.status, alertsmod.MESSAGES[exc.key])
    return {"ok": True, "delivered": took}


@app.get("/api/admin/alerts/history", dependencies=[Depends(require("alerts.history"))])
async def admin_alerts_history(before: int | None = None, limit: int = alerthistory.PAGE):
    """Who sent what, when, to which targets, how long until it was seen, and the reply (#217), newest first."""
    try:
        return await asyncio.to_thread(alerthistory.search, before=before, limit=limit)
    except db.Unavailable:
        raise HTTPException(503, "The database is unavailable, so the history cannot be read.")


# A check-in, rename, feed or forget reaches the open operator panels at once,
# over the same stream as the status. The stream's own 1 s tick covers a panel
# going quiet (see admin_status_stream).
panel_registry.on_change = lambda: hub.push_status(_panels_frame())


@app.get("/api/admin/panels", dependencies=[Depends(require("panels.view"))])
async def admin_panels():
    return _panels_frame()


# Firmware for the panels. These are declared before `/api/admin/panels/{panel_id}`, which would
# otherwise take "firmware" for a panel's id.
@app.get("/api/admin/panels/firmware", dependencies=[Depends(require("panels.view"))])
async def admin_panel_firmware(board: str = ""):
    info = panelfw.current(_board_arg(board))
    return {"firmware": info.public() if info else None, "boards": _firmware_boards()}


@app.post("/api/admin/panels/firmware", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_firmware_upload(request: Request, filename: str = "", board: str = ""):
    """Keep the uploaded image (the raw body) as its board's firmware. It is checked as far as Relay
    can -- a panel's application for a board that is not on hold (and the one `board` names, if it
    names one), a readable version, whole and signed -- and refused with a sentence if not. Whether the
    signature is the right one is the panel's to say."""
    want = _board_arg(board) if board else None
    raw = await _read_limited(request, panelfw.SLOT_BYTES)
    try:
        info = await asyncio.to_thread(panelfw.store, raw, filename, want)
    except panelfw.ImageRefused as exc:
        raise HTTPException(400, str(exc))
    panel_registry.firmware_changed(info.board)
    log.info("Panel firmware %s for %s uploaded (%d bytes)", info.version, info.board, info.size)
    return {"firmware": info.public()}


@app.delete("/api/admin/panels/firmware", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_firmware_delete(board: str = ""):
    board = _board_arg(board)
    had = await asyncio.to_thread(panelfw.remove, board)
    panel_registry.firmware_changed(board)
    return {"ok": True, "removed": had}


@app.post("/api/admin/panels/firmware/update", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_firmware_update(payload: dict):
    """Ask one panel (`id`) or every online panel of a board that has an update available (no `id`;
    `board`, or the CrowPanel) to install its board's stored image. `now` skips the wait for a quiet
    moment."""
    now = payload.get("now") is True
    target = payload.get("id")
    if target is None:
        board = _board_arg(str(payload.get("board") or ""))
        if panelfw.current(board) is None:
            raise HTTPException(409, f"no firmware has been uploaded for the {panelfw.board_label(board)}")
        return panel_registry.request_update_all(now, board)
    try:
        return {"panel": panel_registry.request_update(target, now)}
    except KeyError:
        raise HTTPException(404, "unknown panel")
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# The USB image for the board flasher at `/admin/flash`: the merged `relay-display.bin`, never offered to a
# panel over the network. Admin-only like every route here; the flasher page fetches it with the session.
@app.get("/api/admin/panels/flash", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_flash(board: str = ""):
    info = panelfw.current_flash(_board_arg(board))
    return {"flash": info.public() if info else None}


@app.post("/api/admin/panels/flash", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_flash_upload(request: Request, filename: str = "", board: str = ""):
    """Keep the uploaded merged image (the raw body) as its board's USB image, checked as an update's
    image is, through the application inside it."""
    want = _board_arg(board) if board else None
    raw = await _read_limited(request, panelfw.FLASH_BYTES)
    try:
        info = await asyncio.to_thread(panelfw.store_flash, raw, filename, want)
    except panelfw.ImageRefused as exc:
        raise HTTPException(400, str(exc))
    log.info("Panel USB image %s for %s uploaded (%d bytes)", info.version, info.board, info.size)
    return {"flash": info.public()}


@app.delete("/api/admin/panels/flash", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_flash_delete(board: str = ""):
    had = await asyncio.to_thread(panelfw.remove_flash, _board_arg(board))
    return {"ok": True, "removed": had}


@app.get("/api/admin/panels/flash/{name}", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_flash_file(name: str):
    """`<board>.json`, the manifest the page checks the bytes against, and `<board>.bin`, the bytes."""
    board, _, suffix = name.rpartition(".")
    b = panelfw.BOARDS.get(board)
    info = panelfw.current_flash(board) if b is not None and not b.on_hold else None
    if info is None or suffix not in ("json", "bin"):
        raise HTTPException(404, "no such image")
    if suffix == "json":
        return JSONResponse(info.manifest(), headers={"Cache-Control": "no-store"})
    path = panelfw.flash_path(board)
    if path is None:
        raise HTTPException(404, "no such image")
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@app.post("/api/admin/panels/{panel_id}", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_update(panel_id: str, payload: dict):
    """Rename a panel and/or set its feed. `feeds` is a list of one or two
    {"stream", "lang"}, or null to stop overriding the panel's own choice.
    `service_mode` is off / show / control / both; `service_type` a followed
    service type id, or null to let the panel pick."""
    kwargs: dict = {}
    if "name" in payload:
        if not isinstance(payload["name"], str):
            raise HTTPException(400, "name must be a string")
        kwargs["name"] = payload["name"]
    if "feeds" in payload:
        kwargs["feeds"] = payload["feeds"]
    if "service_mode" in payload:
        if not isinstance(payload["service_mode"], str):
            raise HTTPException(400, "service_mode must be a string")
        kwargs["service_mode"] = payload["service_mode"]
    # Empty while the Planning Center module is off: no panel can be set to a service then.
    followed = (modules_pkg.using("plan.service_types") or (lambda: []))()
    if "service_type" in payload:
        st = payload["service_type"]
        if st not in (None, "") and str(st) not in followed:
            raise HTTPException(400, "Relay does not follow that service type")
        kwargs["service_type"] = st
    if isinstance(payload.get("feeds"), list):
        for feed in payload["feeds"]:
            if (isinstance(feed, dict) and feed.get("stream") == panels.SERVICE
                    and str(feed.get("service_type") or "") not in followed):
                raise HTTPException(400, "Relay does not follow that service type")
    if "rotation" in payload:
        kwargs["rotation"] = payload["rotation"]
    for key in ("lock", "pin", "unlock_min", "return_min"):   # #126; panels.update checks each
        if key in payload:
            kwargs[key] = payload[key]
    if "timers" in payload:
        if payload["timers"] is not None:
            problem = panels.timer_fields(payload["timers"])[1]
            if problem or not isinstance(payload["timers"], dict):
                raise HTTPException(400, problem or "timers must be an object, or null")
        kwargs["timers"] = payload["timers"]
    try:
        row = panel_registry.update(panel_id, **kwargs)
        panel_registry.on_timers_change()   # a panel reading the timers is told its new thresholds at once
        return row
    except KeyError:
        raise HTTPException(404, "unknown panel")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/admin/panels/{panel_id}/reboot", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_reboot(panel_id: str):
    """Restart a panel now. It must be online with its event stream open: a restart is never queued."""
    try:
        row = panel_registry.reboot(panel_id)
    except KeyError:
        raise HTTPException(404, "unknown panel")
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    log.info("Panel %s asked to restart", row["name"])
    return {"panel": row}


@app.delete("/api/admin/panels/{panel_id}", dependencies=[Depends(require("panels.manage"))])
async def admin_panel_forget(panel_id: str):
    if not panel_registry.forget(panel_id):
        raise HTTPException(404, "unknown panel")
    return {"ok": True}


def _schedule_error(exc: ValueError) -> JSONResponse:
    # A fixed sentence by key, never the exception's own text.
    key = exc.key if isinstance(exc, schedules.ScheduleError) else ""
    return JSONResponse({"error": schedules.MESSAGES.get(key, "That schedule is not valid.")}, status_code=400)


async def _save_schedules(items: list[dict], engine_refs: list[str] | None = None) -> dict:
    updates: dict = {"schedules": items}
    if engine_refs is not None:
        updates["engine_schedules"] = engine_refs
    config.save(updates)
    await scheduler.poke()
    engine._push_status()
    return scheduler.listing()


def _engine_refs(sid: str, payload: dict) -> list[str]:
    """The engine's picked schedules after a save, with `sid` in or out as the payload's `engine` says (unchanged
    when it does not say)."""
    refs = [r for r in config.get()["engine_schedules"] if r != schedules.ref(sid)]
    if payload.get("engine", schedules.ref(sid) in config.get()["engine_schedules"]):
        refs.append(schedules.ref(sid))
    return refs


@app.get("/api/admin/schedules", dependencies=[Depends(require("schedules.view"))])
async def admin_schedules():
    return scheduler.listing()


@app.post("/api/admin/schedules", dependencies=[Depends(require("schedules.manage"))])
async def admin_schedule_create(payload: dict):
    items = list(config.get()["schedules"])
    taken = {s["id"] for s in items}
    try:
        new = schedules.validate({k: v for k, v in payload.items() if k != "id"})
    except ValueError as exc:
        return _schedule_error(exc)
    while new["id"] in taken:
        new["id"] = secrets.token_hex(4)
    items.append(new)
    return await _save_schedules(items, _engine_refs(new["id"], payload))


@app.put("/api/admin/schedules/{sid}", dependencies=[Depends(require("schedules.manage"))])
async def admin_schedule_update(sid: str, payload: dict):
    items = list(config.get()["schedules"])
    idx = next((i for i, s in enumerate(items) if s["id"] == sid), None)
    if idx is None:
        raise HTTPException(404, "unknown schedule")
    try:
        items[idx] = schedules.validate(payload, sid=sid)
    except ValueError as exc:
        return _schedule_error(exc)
    return await _save_schedules(items, _engine_refs(sid, payload))


@app.delete("/api/admin/schedules/{sid}", dependencies=[Depends(require("schedules.manage"))])
async def admin_schedule_delete(sid: str):
    items = list(config.get()["schedules"])
    kept = [s for s in items if s["id"] != sid]
    if len(kept) == len(items):
        raise HTTPException(404, "unknown schedule")
    # A schedule something still picks is not deleted out from under it: the page lists where it is used.
    users = scheduler_mod.used_by(sid)
    if users:
        return JSONResponse({"error": schedules.MESSAGES["in_use"], "used_by": users}, status_code=409)
    return await _save_schedules(kept)


# ---------------------------------------------------------------- screens and places (#527)
# One record per physical display (app/screens.py); the Screens page is the live wall: what each screen shows now,
# grouped by place, with what it should show, a hand-held override and a flash.

def _screens_frame() -> dict:
    """The Screens page: places and their screens with what each shows, plus the boards, playlists and schedules a
    screen can be set to."""
    signage = modules_pkg.using("signage.content")
    cfg_boards = {b["id"]: b["name"] for b in config.get()["boards"]}
    lists = {"board": cfg_boards, "signage": {}}
    if signage is not None:
        lists["signage"] = {str(p["id"]): p["name"] for p in signage.store.playlists()}
    now, rows = time.time(), []
    for r in screens.listing():
        shows = _screen_showing(r)
        slug = signage.store.core_screen_slug(r["id"]) if signage is not None else ""
        rows.append({
            "id": r["id"], "ref": r["ref"], "name": r["name"], "kind": r["kind"], "place_id": r["place_id"],
            "place": r["place"], "address": r["address"], "last_seen": r["last_seen"],
            "online": screens.online(r, now),
            "normal": {"kind": r["normal_kind"], "ref": r["normal_ref"]},
            "during": {"kind": r["during_kind"], "ref": r["during_ref"]}, "schedule": r["during_schedule"],
            "hold": {"kind": r["hold_kind"], "ref": r["hold_ref"]},
            "now": {**shows, "label": lists.get(shows["kind"], {}).get(shows["ref"], "")},
            "links": ([signage.store.player_path(slug)] if slug else [])
            + [f"/screen?alert={k[6:]}" for k in r["keys"] if k.startswith("alert:")]})
    return {"places": screens.places(), "screens": rows, "signage_on": signage is not None,
            "boards": [{"id": i, "name": n} for i, n in lists["board"].items()],
            "playlists": [{"id": i, "name": n} for i, n in lists["signage"].items()],
            "schedules": [{"ref": schedules.ref(x["id"]), "name": x["name"]} for x in config.get()["schedules"]],
            "limits": {"name": screens.NAME_MAX}, "now": now}


def _screens_error(exc: screens.Refused) -> JSONResponse:
    return JSONResponse({"error": screens.REFUSALS[exc.key]}, status_code=404 if exc.key.startswith("no_") else 400)


def _shows_pair(raw) -> tuple | None:
    """("board", "lobby") from {"kind", "ref"}; None when the body does not mention it."""
    if not isinstance(raw, dict):
        return None
    return str(raw.get("kind") or ""), str(raw.get("ref") or "")


def _apply_screen_changes(sid: int, payload: dict) -> int:
    """Make the changes in `payload` ({name, place_id, normal, during, schedule}); the id of the screen that results
    (a name another screen has merges the two)."""
    if "place_id" in payload:
        place = payload["place_id"]
        screens.set_place(sid, place if isinstance(place, int) and not isinstance(place, bool) else None)
    if any(k in payload for k in ("normal", "during", "schedule")):
        screens.set_shows(sid, normal=_shows_pair(payload.get("normal")), during=_shows_pair(payload.get("during")),
                          schedule=payload.get("schedule") if isinstance(payload.get("schedule"), str) else None)
    if "name" in payload:
        sid = screens.rename(sid, payload["name"])
    _prepare_signage(sid)
    return sid


def _prepare_signage(sid: int) -> None:
    """A screen set to show a playlist needs a signage screen to play it: make one when it has none."""
    signage = modules_pkg.using("signage.content")
    core = screens.get(sid)
    if signage is None or core is None:
        return
    wants = "signage" in (core["normal_kind"], core["during_kind"], core["hold_kind"])
    if wants:
        signage.store.ensure_screen(core)


@app.get("/api/admin/screens", dependencies=[Depends(require("screens.view"))])
async def admin_screens():
    return await asyncio.to_thread(_screens_frame)


@app.put("/api/admin/screens/{sid}", dependencies=[Depends(require("screens.manage"))])
async def admin_screen_update(sid: int, payload: dict):
    try:
        await asyncio.to_thread(_apply_screen_changes, sid, payload)
    except screens.Refused as exc:
        return _screens_error(exc)
    return await asyncio.to_thread(_screens_frame)


@app.delete("/api/admin/screens/{sid}", dependencies=[Depends(require("screens.manage"))])
async def admin_screen_delete(sid: int):
    try:
        await asyncio.to_thread(screens.delete, sid)
    except screens.Refused as exc:
        return _screens_error(exc)
    return await asyncio.to_thread(_screens_frame)


@app.put("/api/admin/screens/{sid}/hold", dependencies=[Depends(require("screens.manage"))])
async def admin_screen_hold(sid: int, payload: dict):
    """Put a board or signage playlist on the screen now; it stays until cleared."""
    pair = _shows_pair(payload) or ("", "")
    try:
        await asyncio.to_thread(screens.set_hold, sid, *pair)
        await asyncio.to_thread(_prepare_signage, sid)
    except screens.Refused as exc:
        return _screens_error(exc)
    return await asyncio.to_thread(_screens_frame)


@app.delete("/api/admin/screens/{sid}/hold", dependencies=[Depends(require("screens.manage"))])
async def admin_screen_hold_clear(sid: int):
    try:
        await asyncio.to_thread(screens.set_hold, sid)
    except screens.Refused as exc:
        return _screens_error(exc)
    return await asyncio.to_thread(_screens_frame)


def _flash_screen(core: dict) -> int:
    """Show "Test from Relay" on a screen that listens for alerts: a panel by its id, a browser by its alert name."""
    took = 0
    for key in screens.keys(core["id"]):
        if key.startswith("panel:"):
            took += alerts.test(panel=key[6:])
    took += alerts.test(screen=core["name"]) if core["kind"] == "browser" else 0
    return took


@app.post("/api/admin/screens/{sid}/flash", dependencies=[Depends(require("screens.manage"))])
async def admin_screen_flash(sid: int):
    core = await asyncio.to_thread(screens.get, sid)
    if core is None:
        return _screens_error(screens.Refused("no_screen"))
    return {"took": await asyncio.to_thread(_flash_screen, core)}


@app.post("/api/admin/places", dependencies=[Depends(require("screens.manage"))])
async def admin_place_create(payload: dict):
    try:
        await asyncio.to_thread(screens.place_add, payload.get("name"))
    except screens.Refused as exc:
        return _screens_error(exc)
    return await asyncio.to_thread(_screens_frame)


@app.put("/api/admin/places/{pid}", dependencies=[Depends(require("screens.manage"))])
async def admin_place_update(pid: int, payload: dict):
    try:
        await asyncio.to_thread(screens.place_rename, pid, payload.get("name"))
    except screens.Refused as exc:
        return _screens_error(exc)
    # A board keeps the place's name as it was saved: carry the new one over.
    new = next((p["name"] for p in screens.places() if p["id"] == pid), "")
    ref = screens.place_ref(pid)
    kept = [{**b, "place": new} if b["place_ref"] == ref else b for b in config.get()["boards"]]
    if kept != config.get()["boards"]:
        config.save({"boards": kept})
    return await asyncio.to_thread(_screens_frame)


@app.delete("/api/admin/places/{pid}", dependencies=[Depends(require("screens.manage"))])
async def admin_place_delete(pid: int):
    try:
        await asyncio.to_thread(screens.place_delete, pid)
    except screens.Refused as exc:
        return _screens_error(exc)
    kept = [{**b, "place": "", "place_ref": ""} if b["place_ref"] == screens.place_ref(pid) else b
            for b in config.get()["boards"]]
    if kept != config.get()["boards"]:
        config.save({"boards": kept})
    return await asyncio.to_thread(_screens_frame)


@app.post("/api/admin/places/{pid}/flash", dependencies=[Depends(require("screens.manage"))])
async def admin_place_flash(pid: int):
    found = await asyncio.to_thread(screens.in_place, pid)
    took = 0
    for r in found:
        core = await asyncio.to_thread(screens.get, r["id"])
        took += await asyncio.to_thread(_flash_screen, core) if core else 0
    return {"took": took}


# ---------------------------------------------------------------- overlays

def _overlays_frame() -> dict:
    """What the Broadcast overlay page needs: the saved overlays, and the
    options the builder offers (presets, defaults, languages)."""
    src = config.get()["source_language"]
    return {
        "overlays": config.get()["overlays"],
        "defaults": overlays.DEFAULTS,
        "presets": overlays.PRESETS,
        "targets": [{"target": t, "label": languages.target_label(t)} for t in languages.target_names(src)],
        "live": [t["target"] for t in engine.live_targets()],
        "source_label": languages.SOURCE_LANGUAGES[src]["label"],
    }


def _overlay_error(exc: overlays.OverlayError) -> JSONResponse:
    # A fixed sentence by key, never the exception's own text.
    return JSONResponse({"error": overlays.MESSAGES.get(exc.key, "That overlay is not valid.")}, status_code=400)


@app.get("/api/admin/overlays", dependencies=[Depends(require("overlays.view"))])
async def admin_overlays():
    return _overlays_frame()


@app.post("/api/admin/overlays", dependencies=[Depends(require("overlays.manage"))])
async def admin_overlay_create(payload: dict):
    items = list(config.get()["overlays"])
    try:
        new = overlays.validate(payload, languages.TARGETS, {o["id"] for o in items})
    except overlays.OverlayError as exc:
        return _overlay_error(exc)
    items.append(new)
    config.save({"overlays": items})
    return _overlays_frame() | {"saved": new["id"]}


@app.put("/api/admin/overlays/{oid}", dependencies=[Depends(require("overlays.manage"))])
async def admin_overlay_update(oid: str, payload: dict):
    items = list(config.get()["overlays"])
    idx = next((i for i, o in enumerate(items) if o["id"] == oid), None)
    if idx is None:
        raise HTTPException(404, "unknown overlay")
    try:
        items[idx] = overlays.validate(payload, languages.TARGETS,
                                       {o["id"] for o in items if o["id"] != oid}, oid=oid)
    except overlays.OverlayError as exc:
        return _overlay_error(exc)
    config.save({"overlays": items})
    return _overlays_frame() | {"saved": items[idx]["id"]}


@app.delete("/api/admin/overlays/{oid}", dependencies=[Depends(require("overlays.manage"))])
async def admin_overlay_delete(oid: str):
    items = list(config.get()["overlays"])
    kept = [o for o in items if o["id"] != oid]
    if len(kept) == len(items):
        raise HTTPException(404, "unknown overlay")
    config.save({"overlays": kept})
    return _overlays_frame()


# ---------------------------------------------------------------- boards (#156)
# A board is a screen built from widgets at /board/<slug> (app/boards.py), laid out with the shared editor (#421).
# Not an admin path, so it answers on both ports: a public board opens for anyone, on either; any other opens only on
# the operator port, for someone signed in with boards.view (every role). Its widgets' data comes from the existing
# feeds (/stream) and, for a module's widget, from /board/<slug>/_/data, which asks the module through the registry.
# That poll also says which page to show (boards.current), names the screen asking (so the Boards page lists the
# screens showing each board), and carries the board's revision and a reload count, so a saved change or "Reload all
# screens" reaches every screen without anyone touching it.
BOARD_PRESS_WINDOW_S = 10.0
BOARD_MAX_PRESSES = 20
BOARD_SCREEN_GONE_S = 15.0       # a screen that has not asked for this long is no longer showing the board
BOARD_MAX_LIVE_SCREENS = 100
_board_presses: dict[str, list[float]] = {}
_board_live: dict[str, dict] = {}   # board id -> {"screens": {id: {first, last, ip}}, "reload", "override", "last"}
# Refusals the board routes answer with: fixed sentences, looked up by key.
BOARD_REFUSALS = {"no_page": "That page is not on this board.", "no_screen": "That is not a screen.",
                  "too_many_names": "This board has too many named screens."}


def _board_catalogue() -> dict[str, dict]:
    return boards.catalogue(module_registry.board_widgets(), config.get()["boards"])


def _board_or_404(bid: str) -> dict:
    board = boards.find(config.get()["boards"], bid)
    if board is None:
        raise HTTPException(404, "Not Found")
    return board


def _board_viewer(request: Request, board: dict, perm: str = "boards.view"):
    """(user or None, None) when this request may open the board, else (None, the response to send). A public board
    needs nobody signed in; any other needs the operator port and `perm`. Blocks (database)."""
    if board["public"] and perm == "boards.view":
        return (_session_user(request) if _on_admin_port(request) else None), None
    if not _on_admin_port(request):
        return None, HTMLResponse("Not found", status_code=404)
    got = _page_gate(request, perm)
    return (got, None) if isinstance(got, User) else (None, got)


def _board_items_for_page(items: list[dict], cat: dict[str, dict]) -> list[dict]:
    """Items with what the page draws them by: the kind and the title, or a note when the widget's module is off."""
    out = []
    for it in items:
        d = cat.get(it["type"])
        extra = {"kind": d["kind"], "title": boards.item_title(d, it["props"])} if d else \
            {"kind": "text", "title": "", "off": "This widget's module is off."}
        out.append({**it, **extra})
    return out


def _board_rev(board: dict) -> str:
    """Changes whenever what a screen draws changes (not the screen names)."""
    body = json.dumps({k: board[k] for k in ("name", "ratio", "cols", "rows", "pages", "rotation")}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def _board_live_for(bid: str) -> dict:
    return _board_live.setdefault(bid, {"screens": {}, "reload": 0, "override": None, "last": None})


def _local_now() -> datetime:
    """Now as naive wall-clock time in the one Relay zone (Settings > System)."""
    return datetime.now(ZoneInfo(config.timezone())).replace(tzinfo=None)


def _schedule_open(ref: str, local_now: datetime) -> bool:
    """Whether the named schedule `ref` is enabled and open at `local_now` (a board row that names a schedule)."""
    sched = schedules.find(config.get()["schedules"], ref)
    return bool(sched and sched["enabled"]
                and schedules.is_open_local(sched, local_now, config.timezone(), scheduler_plan_source()))


def _board_state(board: dict, follow: dict[str, dict] | None = None) -> dict:
    """Which page the board shows now, as boards.current decides it, unless someone chose one by hand from the Boards
    page: that holds until what decides the page changes (the schedule moves on, ProPresenter shows something else)."""
    live = _board_live_for(board["id"])
    rot = board["rotation"]
    now_pp = None
    if rot["mode"] == "follow":
        src = (follow if follow is not None else module_registry.board_follow()).get(rot["follow"]["source"])
        now_pp = src["now"] if src and src["connected"] else None
    cur = boards.current(board, _local_now(), now_pp, live["last"], _schedule_open)
    ov = live["override"]
    ids = [p["id"] for p in board["pages"]]
    if ov and (ov["key"] != cur["key"] or ov["page"] not in ids):
        live["override"] = ov = None
    if ov:
        cur = {**cur, "page": ov["page"], "rotate": None, "override": True,
               "why": f"Chosen by hand: {boards.show_label(board, ov['page'], True)}, until the next change"}
    if not cur["rotate"]:
        live["last"] = cur["page"]
    return cur


def _board_screens(bid: str) -> list[dict]:
    """The screens asking for this board, named from the core screen each one is (app/screens.py)."""
    live = _board_live_for(bid)
    now = time.time()
    for sid in [s for s, v in live["screens"].items() if now - v["last"] > BOARD_SCREEN_GONE_S]:
        live["screens"].pop(sid, None)
    try:
        names = screens.names_by_key("board:")
    except db.Unavailable:
        names = {}
    rows = [{"id": sid, "name": names.get(sid, ""), "ip": v["ip"], "since": v["first"]}
            for sid, v in live["screens"].items()]
    return sorted(rows, key=lambda r: (not r["name"], r["name"].lower(), r["since"]))


BOARD_CORE_SCREEN = re.compile(r"screen-(\d{1,9})")
BOARD_SCREEN_REFRESH_S = 15.0   # how often a polling board screen tells the database it is still there


def _board_screen_key(sid: str) -> str:
    return "board:" + sid


def _signage_on() -> bool:
    return modules_pkg.using("signage.content") is not None


def _screen_showing(screen: dict) -> dict:
    """What a core screen shows now (app/screens.py `showing_now`)."""
    return screens.showing_now(screen, signage_on=_signage_on())


def _board_screen_goto(request: Request, board: dict, sid: str, row: dict) -> str:
    """Tell the database this browser is showing the board, then say where it should go instead, when its screen is
    set to show a signage playlist or another board ("" to stay). A browser opened from a screen's own link
    (`screen-<id>`, see below) is that screen; any other is adopted by its random id, named by what the board last
    called it."""
    try:
        if row.get("core") is None or time.time() - row.get("synced", 0) > BOARD_SCREEN_REFRESH_S:
            m = BOARD_CORE_SCREEN.fullmatch(sid)
            core = screens.get(int(m.group(1))) if m else None
            if core is None:
                core = screens.adopt(_board_screen_key(sid), name=board["screen_names"].get(sid, ""),
                                     address=row["ip"], board=board["id"])
            else:
                screens.touch(core["id"], address=row["ip"], board=board["id"])
            row["core"], row["synced"] = core["id"], time.time()
        core = screens.get(row["core"])
    except db.Unavailable:
        return ""
    if core is None:
        row["core"] = None
        return ""
    shows = _screen_showing(core)
    if shows["kind"] == "board" and shows["ref"] and shows["ref"] != board["id"] \
            and boards.find(config.get()["boards"], shows["ref"]):
        return f"/board/{shows['ref']}?screen=screen-{core['id']}"
    signage = modules_pkg.using("signage.content")
    if shows["kind"] == "signage" and signage is not None:
        slug = signage.store.core_screen_slug(core["id"])
        if slug:
            return signage.store.player_address(request.url.hostname or "", slug)
    return ""


@app.get("/board/{bid}", response_class=HTMLResponse)
def page_board(request: Request, bid: str, edit: int = 0):
    board = _board_or_404(bid)
    user, resp = _board_viewer(request, board, "boards.manage" if edit else "boards.view")
    if resp is not None:
        return resp
    if edit:
        # Boards are laid out on the Boards page now; an old ?edit=1 link goes there.
        return RedirectResponse("/admin#boards", status_code=303)
    cat = _board_catalogue()
    perms = user.permissions() if user else set()
    page = {"id": board["id"], "name": board["name"], "ratio": board["ratio"], "cols": board["cols"],
            "rows": board["rows"], "public": board["public"], "rev": _board_rev(board),
            "pages": [{"id": p["id"], "name": p["name"], "items": _board_items_for_page(p["items"], cat)}
                      for p in board["pages"]]}
    # Which widget types this person may press (a module's buttons need its permission; a board link needs nothing).
    press = sorted(t for t, d in cat.items() if d["kind"] in boards.PRESSED and (not d["press_permission"]
                                                                            or d["press_permission"] in perms))
    ctx = {"board": page, "press": press, "state": _board_state(board)}
    return templates.TemplateResponse(request, "board.html", {"data": ctx, "title": board["name"]},
                                      headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/board/{bid}/_/data")
async def board_data(request: Request, bid: str, screen: str = ""):
    """Live data for the board's module widgets (every page's), by item id, the page to show now, and the board's
    revision and reload count. `screen` is the asking screen's own id. Same access as the board."""
    board = _board_or_404(bid)
    _, resp = await asyncio.to_thread(_board_viewer, request, board)
    if resp is not None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    live = _board_live_for(bid)
    goto = ""
    if boards.SCREEN_ID.fullmatch(screen or ""):
        now = time.time()
        row = live["screens"].get(screen)
        if row is not None:
            row["last"] = now
        elif len(live["screens"]) < BOARD_MAX_LIVE_SCREENS:
            row = live["screens"][screen] = {"first": now, "last": now, "ip": _client_ip(request)}
        if row is not None:
            goto = await asyncio.to_thread(_board_screen_goto, request, board, screen, row)
    data = await module_registry.board_data(boards.all_items(board))
    st = _board_state(board)
    return JSONResponse({"items": data, "page": st["page"], "rotate": st["rotate"], "now": time.time(),
                         "rev": _board_rev(board), "reload": live["reload"], "goto": goto},
                        headers={"Cache-Control": "no-store"})


async def _press_route(item: dict, request: Request) -> dict:
    """A route button was pressed: the destination takes the source, the same route the NDI and Blackmagic pages make."""
    props = item.get("props") or {}
    if not props.get("destination") or not props.get("source"):
        return {"ok": False, "error": "Choose the destination and the source for this button."}
    got = await _routes_do(routing.set, props["destination"], props["source"])
    if isinstance(got, JSONResponse):
        return {"ok": False, "error": "That route could not be made."}
    if got["problems"]:
        return {"ok": False, "error": got["problems"][0][:200]}
    audit.note(request, "routes.set", "Routed a source to a destination",
               detail={"destination": props["destination"], "source": props["source"]})
    return {"ok": True}


@app.post("/board/{bid}/_/press/{item_id}")
async def board_press(request: Request, bid: str, item_id: str):
    """Press a module's button widget on a board, or move one of its faders (the module reads the JSON body, such as
    {"strip": "in3", "pos": 0.6}). Needs someone signed in on the operator port who holds the widget's press permission,
    even on a public board. Answers {"ok": true} or {"error": "<a fixed sentence>"}."""
    board = _board_or_404(bid)
    item = boards.find_item(board, item_id)
    if item is None:
        return JSONResponse({"error": "That button is not on this board."}, status_code=404)
    d = _board_catalogue().get(item["type"])
    mod = module_registry.board_owner(item["type"])
    core_route = item["type"] == "route"          # core's own button (app/routing.py), not a module's
    if d is None or (mod is None and not core_route) or d["kind"] not in boards.PRESSED:
        return JSONResponse({"error": "That button is off."}, status_code=409)
    user = await asyncio.to_thread(_session_user, request) if _on_admin_port(request) else None
    if user is None or (not board["public"] and not user.can("boards.view")) \
            or (d["press_permission"] and not user.can(d["press_permission"])):
        return JSONResponse({"error": "Sign in with an account that may press this."}, status_code=403)
    if _rate_limited(_board_presses, _client_ip(request), BOARD_MAX_PRESSES, BOARD_PRESS_WINDOW_S):
        return JSONResponse({"error": "Too many presses. Wait a moment."}, status_code=429)
    request.state.user = user
    try:
        got = await (_press_route(item, request) if core_route else mod.board_press(item, request))
    except Exception:
        log.exception("board press failed")
        got = {"ok": False, "error": "That did not work."}
    if isinstance(got, dict) and got.get("ok"):
        # Not under /api/admin, so the audit middleware does not see it: write the module's notes here.
        notes = audit.pending(request) or [{"action": "boards.press", "summary": f"Pressed {d['name']} on the board "
                                            f"{board['name']}", "target": item["id"], "detail": None}]
        await asyncio.to_thread(_audit_write, notes, user, _client_ip(request))
        return {"ok": True}
    err = got.get("error") if isinstance(got, dict) and isinstance(got.get("error"), str) else "That did not work."
    return JSONResponse({"error": err[:200]}, status_code=502)


def _rate_limited(table: dict[str, list[float]], who: str, limit: int, window: float) -> bool:
    now = time.monotonic()
    if len(table) > 2000:
        for k in [k for k, v in table.items() if not v or now - v[-1] > window]:
            table.pop(k, None)
    recent = [t for t in table.get(who, []) if now - t < window]
    if len(recent) >= limit:
        table[who] = recent
        return True
    table[who] = recent + [now]
    return False


def _boards_live() -> dict:
    """What the Boards page shows live: per board, the page up now and why, and the screens showing it; and the follow
    sources a board can follow (ProPresenter)."""
    follow = module_registry.board_follow()
    out = {}
    for b in config.get()["boards"]:
        st = _board_state(b, follow)
        out[b["id"]] = {"page": st["page"], "rotate": st["rotate"], "why": st["why"], "override": bool(st.get("override")),
                        "screens": _board_screens(b["id"])}
    return {"live": out, "follow": follow, "now": time.time()}


def _link_board_places(items: list[dict]) -> list[dict]:
    """Each board's `place` text is a core place (app/screens.py, #527): make the place when it is new and keep its ref
    and its current name on the board. The same list back when nothing changed."""
    changed, out = False, []
    for b in items:
        if b["place"] and not b["place_ref"]:
            try:
                found = screens.place_add(b["place"])
            except (db.Unavailable, screens.Refused):
                out.append(b)
                continue
            b, changed = {**b, "place": found["name"], "place_ref": found["ref"]}, True
        out.append(b)
    return out if changed else items


def _boards_frame() -> dict:
    stored = config.get()["boards"]
    linked = _link_board_places(stored)
    if linked is not stored:
        config.save({"boards": linked})
    cat = _board_catalogue()
    return {"boards": config.get()["boards"], "catalogue": list(cat.values()), "templates": boards.templates(cat),
            "days": boards.DAYS, "matches": boards.MATCHES, "max_pages": boards.MAX_PAGES,
            "schedules": [{"ref": schedules.ref(x["id"]), "name": x["name"]} for x in config.get()["schedules"]],
            "swatches": boards.SWATCHES} | _boards_live()


def _board_error(exc: boards.BoardError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/admin/boards", dependencies=[Depends(require("boards.view"))])
async def admin_boards():
    return _boards_frame()


@app.get("/api/admin/boards/live", dependencies=[Depends(require("boards.view"))])
async def admin_boards_live():
    return _boards_live()


@app.post("/api/admin/boards", dependencies=[Depends(require("boards.manage"))])
async def admin_board_create(payload: dict):
    items = list(config.get()["boards"])
    try:
        new = boards.create(items, payload, _board_catalogue())
    except boards.BoardError as exc:
        return _board_error(exc)
    config.save({"boards": _link_board_places(items + [new])})
    return _boards_frame() | {"saved": new["id"]}


@app.put("/api/admin/boards/{bid}", dependencies=[Depends(require("boards.manage"))])
async def admin_board_update(bid: str, payload: dict):
    items = list(config.get()["boards"])
    idx = next((i for i, b in enumerate(items) if b["id"] == bid), None)
    if idx is None:
        raise HTTPException(404, "unknown board")
    try:
        items[idx] = boards.update(items[idx], payload, _board_catalogue())
    except boards.BoardError as exc:
        return _board_error(exc)
    if "place" in payload:
        items[idx] = {**items[idx], "place_ref": ""}   # a new place text is looked up again below
    items = _link_board_places(items)
    config.save({"boards": items})
    return _boards_frame() | {"saved": bid, "board": items[idx]}


@app.put("/api/admin/boards/{bid}/screens/{sid}", dependencies=[Depends(require("boards.manage"))])
async def admin_board_screen_name(bid: str, sid: str, payload: dict):
    """Name one of the screens showing a board (Foyer TV left), or clear its name with ""."""
    _board_or_404(bid)
    if not boards.SCREEN_ID.fullmatch(sid):
        return JSONResponse({"error": BOARD_REFUSALS["no_screen"]}, status_code=400)
    name = boards.layout.text(payload.get("name"), boards.SCREEN_NAME_MAX)
    if not name:
        return JSONResponse({"error": screens.REFUSALS["name"]}, status_code=400)
    try:
        core = await asyncio.to_thread(screens.adopt, _board_screen_key(sid))
        await asyncio.to_thread(screens.rename, core["id"], name)
    except screens.Refused as exc:
        return JSONResponse({"error": screens.REFUSALS[exc.key]}, status_code=400)
    _board_live_for(bid)["screens"].get(sid, {}).pop("core", None)
    return _boards_frame()


@app.post("/api/admin/boards/{bid}/reload", dependencies=[Depends(require("boards.manage"))])
async def admin_board_reload(bid: str):
    """Every screen showing the board loads it again within a few seconds."""
    _board_or_404(bid)
    _board_live_for(bid)["reload"] += 1
    return _boards_live()


@app.post("/api/admin/boards/{bid}/page", dependencies=[Depends(require("boards.manage"))])
async def admin_board_page(bid: str, payload: dict):
    """Show one page now, by hand ({"page": id}), until what decides the page changes; {"page": ""} goes back to it."""
    board = _board_or_404(bid)
    want = payload.get("page") if isinstance(payload.get("page"), str) else ""
    live = _board_live_for(bid)
    if not want:
        live["override"] = None
        return _boards_live()
    if want not in [p["id"] for p in board["pages"]]:
        return JSONResponse({"error": BOARD_REFUSALS["no_page"]}, status_code=400)
    live["override"] = None
    key = _board_state(board)["key"]
    live["override"] = {"page": want, "key": key}
    return _boards_live()


@app.post("/api/admin/boards/{bid}/duplicate", dependencies=[Depends(require("boards.manage"))])
async def admin_board_duplicate(bid: str):
    items = list(config.get()["boards"])
    board = boards.find(items, bid)
    if board is None:
        raise HTTPException(404, "unknown board")
    try:
        new = boards.create(items, {**board, "name": f"{board['name']} copy"[:boards.NAME_MAX]}, _board_catalogue())
    except boards.BoardError as exc:
        return _board_error(exc)
    config.save({"boards": _link_board_places(items + [new])})
    return _boards_frame() | {"saved": new["id"]}


@app.get("/api/admin/boards/{bid}/export", dependencies=[Depends(require("boards.view"))])
async def admin_board_export(bid: str):
    board = _board_or_404(bid)
    return JSONResponse(boards.export(board), headers={
        "Content-Disposition": f'attachment; filename="{board["id"]}.relayboard.json"'})


@app.post("/api/admin/boards/import", dependencies=[Depends(require("boards.manage"))])
async def admin_board_import(payload: dict):
    items = list(config.get()["boards"])
    try:
        new = boards.import_board(items, payload, _board_catalogue())
    except boards.BoardError as exc:
        return _board_error(exc)
    config.save({"boards": _link_board_places(items + [new])})
    return _boards_frame() | {"saved": new["id"]}


@app.delete("/api/admin/boards/{bid}", dependencies=[Depends(require("boards.manage"))])
async def admin_board_delete(bid: str):
    items = list(config.get()["boards"])
    kept = [b for b in items if b["id"] != bid]
    if len(kept) == len(items):
        raise HTTPException(404, "unknown board")
    config.save({"boards": kept})
    _board_live.pop(bid, None)
    with contextlib.suppress(db.Unavailable):
        await asyncio.to_thread(screens.forget_board, bid)
    return _boards_frame()


# ---------------------------------------------------------------- recordings
# A recorded run is the window between one start and the matching stop. The
# files below are derived from it on download (see exporting.py); the run
# directory itself only ever holds meta.json and lines.jsonl.

def _run_or_404(run_id: str):
    """The run directory for `run_id`, or a 404. `recorder.run_dir` is the only
    thing that turns a request-supplied id into a path, and it accepts nothing
    but a timestamp-shaped id."""
    d = recorder.run_dir(run_id)
    if d is None:
        raise HTTPException(404, "unknown run")
    return d


def _download(body: bytes, filename: str, media: str) -> Response:
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/admin/recordings", dependencies=[Depends(require("recordings.view"))])
async def admin_recordings():
    cfg = config.get()
    return {
        "enabled": cfg["recording"]["enabled"],
        "keep_runs": cfg["recording"]["keep_runs"],
        "dir": str(config.recordings_path()),
        "runs": recorder.list_runs(),
    }


@app.get("/api/admin/recordings/{run_id}", dependencies=[Depends(require("recordings.view"))])
async def admin_recording(run_id: str):
    """One run's metadata plus the files a download would produce, with line
    counts -- enough for the panel to show what is in there before fetching
    several megabytes of transcript."""
    d = _run_or_404(run_id)
    meta = recorder.list_run(run_id) or {"run_id": run_id}
    rows = recorder.read_lines(d)
    files = exporting.build_exports(meta, rows)
    return {
        "run": meta,
        "files": [
            {"name": name, "lines": text.count("\n"), "bytes": len(text.encode())}
            for name, text in sorted(files.items())
        ],
    }


@app.get("/api/admin/recordings/{run_id}/export.zip",
         dependencies=[Depends(require("recordings.view"))])
async def admin_recording_zip(run_id: str):
    d = _run_or_404(run_id)
    meta = recorder.list_run(run_id) or {"run_id": run_id}
    rows = recorder.read_lines(d)
    raw = ""
    with contextlib.suppress(OSError):
        raw = (d / "lines.jsonl").read_text(encoding="utf-8")
    body = exporting.zip_bytes(meta, rows, raw)
    return _download(body, f"relay-{run_id}.zip", "application/zip")


@app.get("/api/admin/recordings/{run_id}/file/{name}",
         dependencies=[Depends(require("recordings.view"))])
async def admin_recording_file(run_id: str, name: str):
    """One derived file. `name` is matched against the set this run actually
    produces -- it is never joined onto a path."""
    d = _run_or_404(run_id)
    meta = recorder.list_run(run_id) or {"run_id": run_id}
    rows = recorder.read_lines(d)
    files = exporting.build_exports(meta, rows)
    text = files.get(name)
    if text is None:
        raise HTTPException(404, "unknown file")
    media = "application/x-ndjson" if name.endswith(".jsonl") else "text/plain"
    return _download(text.encode("utf-8"), f"{run_id}-{name}", media + "; charset=utf-8")


@app.delete("/api/admin/recordings/{run_id}", dependencies=[Depends(require("recordings.manage"))])
async def admin_recording_delete(run_id: str):
    _run_or_404(run_id)
    if not recorder.delete_run(run_id):
        raise HTTPException(409, "that run is still being recorded")
    return {"deleted": run_id, "runs": recorder.list_runs()}


# ------------------------------------------------------- the signed-in user, accounts, trusted devices
def _iso(row: dict) -> dict:
    """A database row for JSON: timestamps as ISO 8601 strings."""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


def _text(payload: dict, key: str):
    """A string field of a JSON body, or None when absent (users.py rejects what is not valid)."""
    v = payload.get(key)
    return v if isinstance(v, str) else None


@app.get("/api/admin/me", dependencies=[Depends(require(None, pending_ok=True))])
def admin_me(request: Request):
    """Any signed-in user, including one who still has to change their password (so the page can tell)."""
    return _me_payload(request, request.state.user)


@app.post("/api/admin/me/password", dependencies=[Depends(require(None, pending_ok=True, tokens=False))])
def admin_me_password(request: Request, payload: dict):
    """Change the signed-in user's own password. Their other sessions end; this browser's stays."""
    users.change_own_password(request.state.user, payload.get("current"), payload.get("new"),
                              keep_token=_session_token(request))
    return {"ok": True}


@app.post("/api/admin/me/pin", dependencies=[Depends(require(None, tokens=False))])
def admin_me_pin(request: Request, payload: dict):
    user: User = request.state.user
    users.set_pin(user, payload.get("password"), payload.get("pin"))
    return {"ok": True, "pin_set": True}


@app.delete("/api/admin/me/pin", dependencies=[Depends(require(None, tokens=False))])
def admin_me_pin_clear(request: Request):
    users.clear_pin(request.state.user.id)
    return {"ok": True, "pin_set": False}


def _pref_scope(scope: str) -> str:
    """A scope the API accepts: core's, or a module that exists (its own code may use any scope it likes)."""
    if scope != prefs.CORE and module_registry.get(scope) is None:
        raise HTTPException(404, "There is no such preference scope.")
    return scope


@app.get("/api/admin/me/prefs", dependencies=[Depends(require(None))])
def admin_me_prefs(request: Request):
    """The signed-in user's own preferences (#173), as {scope: {name: value}}. They follow the person to every
    device. An API token reads and writes its user's, like everything else it may do."""
    uid = request.state.user.id
    return {"prefs": prefs.all_prefs(uid) if uid else {}}


@app.put("/api/admin/me/prefs/{scope}/{name}", dependencies=[Depends(require(None))])
def admin_me_pref_set(request: Request, scope: str, name: str, payload: dict):
    """Set one of the signed-in user's preferences: {"value": <JSON>}. A null value removes it."""
    if "value" not in payload:
        raise HTTPException(400, "Send the preference as {\"value\": ...}.")
    try:
        prefs.set(request.state.user.id, name, payload["value"], scope=_pref_scope(scope))
    except prefs.Invalid as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "scope": scope, "name": name, "value": payload["value"]}


@app.delete("/api/admin/me/prefs/{scope}/{name}", dependencies=[Depends(require(None))])
def admin_me_pref_delete(request: Request, scope: str, name: str):
    try:
        gone = prefs.delete(request.state.user.id, name, scope=_pref_scope(scope))
    except prefs.Invalid as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "deleted": gone}


# Not entered in the audit log: a person's own preferences (#173) are theirs, not a change to Relay, and a page
# saving a layout would bury the log. Matched by the function itself, not the route's name, so a module route
# whose function happens to share a name is still recorded (#287).
AUDIT_SKIP_ENDPOINTS = frozenset({admin_me_pref_set, admin_me_pref_delete})
AUDIT_SKIP = frozenset(f.__name__ for f in AUDIT_SKIP_ENDPOINTS)


@app.get("/api/admin/me/tokens", dependencies=[Depends(require("tokens.own", tokens=False))])
def admin_me_tokens(request: Request):
    return {"tokens": users.list_tokens(request.state.user.id), "expiry_days": list(users.TOKEN_EXPIRY_DAYS),
            "default_days": users.TOKEN_DEFAULT_DAYS}


@app.post("/api/admin/me/tokens", dependencies=[Depends(require("tokens.own", tokens=False))])
def admin_me_token_create(request: Request, payload: dict):
    """A new API token for the signed-in user. The secret is in this answer only; it is never shown again."""
    secret, row = users.create_token(request.state.user, payload.get("name"),
                                     payload.get("days", users.TOKEN_DEFAULT_DAYS))
    audit.note(request, "token.create", "Made an API token", target=row["name"],
               detail={"expires": row["expires"] or "never"})
    return JSONResponse({"token": row, "secret": secret}, headers=_NO_STORE)


@app.delete("/api/admin/me/tokens/{token_id}", dependencies=[Depends(require(None, tokens=False))])
def admin_me_token_revoke(token_id: int, request: Request):
    """Revoke one of your own tokens. Allowed whatever your role, so nobody is left unable to revoke theirs."""
    user: User = request.state.user
    mine = next((t for t in users.list_tokens(user.id) if t["id"] == token_id), None)
    if mine is None:
        raise users.Refused("not_found")
    users.revoke_token(token_id, user)
    audit.note(request, "token.revoke", "Revoked one of their API tokens", target=mine["name"])
    return {"ok": True}


@app.get("/api/admin/tokens", dependencies=[Depends(require("tokens.manage"))])
def admin_tokens():
    """Everyone's API tokens (Owners): who holds each, never the secret."""
    return {"tokens": users.list_tokens()}


@app.delete("/api/admin/tokens/{token_id}", dependencies=[Depends(require("tokens.manage"))])
def admin_token_revoke(token_id: int, request: Request):
    had = next((t for t in users.list_tokens() if t["id"] == token_id), None)
    users.revoke_token(token_id, request.state.user)
    if had is not None:
        audit.note(request, "token.revoke", f"Revoked {had['user_name']}'s API token", target=had["name"],
                   detail={"owner": had["username"]})
    return {"ok": True}


@app.get("/api/admin/users", dependencies=[Depends(require("users.view"))])
def admin_users():
    return {
        "users": [_iso(u) for u in users.list_users()],
        "roles": [{"id": r, "label": r.title(), "rank": i + 1} for i, r in enumerate(users.ROLES)],
        "permissions": [asdict(p) for p in users.catalogue()],
    }


@app.post("/api/admin/users", dependencies=[Depends(require("users.manage"))])
def admin_user_create(request: Request, payload: dict):
    """A new account with a temporary password the person must change at first sign-in."""
    made = users.create(payload.get("username"), payload.get("name"), payload.get("role"),
                        payload.get("password"), temporary=True, actor=request.state.user)
    audit.note(request, "user.create", f"Added {made.name} as {made.role.title()}", target=made.username,
               detail={"role": made.role})
    return {"user": asdict(made)}


@app.put("/api/admin/users/{user_id}", dependencies=[Depends(require("users.manage"))])
def admin_user_update(user_id: int, request: Request, payload: dict):
    changes = {k: payload[k] for k in ("name", "role", "disabled") if k in payload}
    if "disabled" in changes and not isinstance(changes["disabled"], bool):
        raise _bad_request()
    before = users.get(user_id)
    after = users.update(user_id, actor=request.state.user, **changes)
    _note_user_change(request, before, after)
    return {"user": asdict(after)}


def _note_user_change(request: Request, before: User | None, after: User) -> None:
    """What changed about a user, from and to: the role change is the one an audit is most often asked for."""
    changes = {}
    if before is not None:
        for k in ("name", "role", "disabled"):
            if getattr(before, k) != getattr(after, k):
                changes[k] = [getattr(before, k), getattr(after, k)]
    words = []
    if "role" in changes:
        words.append(f"moved {after.name} from {changes['role'][0].title()} to {after.role.title()}")
    if "disabled" in changes:
        words.append(f"turned {after.name} {'off' if after.disabled else 'on'}")
    if "name" in changes:
        words.append(f"renamed {changes['name'][0]} to {after.name}")
    summary = "; ".join(words) if words else f"saved {after.name} unchanged"
    audit.note(request, "user.update", summary[0].upper() + summary[1:], target=after.username,
               detail={"changes": changes})


@app.post("/api/admin/users/{user_id}/password", dependencies=[Depends(require("users.manage"))])
def admin_user_password(user_id: int, request: Request, payload: dict):
    """Reset a password (a forgotten one): temporary, so they change it at next sign-in. Ends their
    sessions and clears their PIN."""
    users.set_password(user_id, payload.get("password"), temporary=True, actor=request.state.user)
    who = users.get(user_id)
    if who is not None:
        audit.note(request, "user.password_reset", f"Reset {who.name}'s password", target=who.username)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/signout", dependencies=[Depends(require("users.manage"))])
def admin_user_signout(user_id: int, request: Request):
    target = users.get(user_id)
    if target is None:
        raise users.Refused("not_found")
    if users.ROLES.index(target.role) > users.ROLES.index(request.state.user.role):
        raise users.Refused("account_above_you")
    users.end_sessions(user_id)
    audit.note(request, "user.signout", f"Signed {target.name} out everywhere", target=target.username)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}", dependencies=[Depends(require("users.manage"))])
def admin_user_delete(user_id: int, request: Request):
    who = users.get(user_id)
    users.delete(user_id, actor=request.state.user)
    if who is not None:
        audit.note(request, "user.delete", f"Deleted {who.name} ({who.role.title()})", target=who.username,
                   detail={"role": who.role})
    return {"ok": True}


@app.get("/api/admin/trusted-devices", dependencies=[Depends(require("users.view"))])
def admin_devices_trusted(request: Request):
    here = users.device_for(request.cookies.get(DEVICE_COOKIE) or "")
    return {"devices": [_iso(d) for d in users.list_devices()], "this": here["id"] if here else None}


@app.post("/api/admin/trusted-devices", dependencies=[Depends(require("users.manage"))])
def admin_device_trust(request: Request, payload: dict):
    """Trust THIS browser: it gets the relay_device cookie, and from then on may sign in with a PIN."""
    user: User = request.state.user
    if user.id <= 0:                       # the setup token has no account to record it against
        raise HTTPException(status_code=403, detail=WEB_MESSAGES["not_allowed"])
    token, row = users.trust_device(payload.get("name"), user, request.headers.get("user-agent", ""))
    audit.note(request, "device.trust", "Trusted a browser for PIN sign-in", target=row["name"])
    resp = JSONResponse({"device": _iso(row), "this": row["id"]})
    _set_cookie(resp, request, DEVICE_COOKIE, token, DEVICE_COOKIE_MAX_AGE)
    return resp


@app.delete("/api/admin/trusted-devices/{device_id}", dependencies=[Depends(require("users.manage"))])
def admin_device_revoke(device_id: int, request: Request):
    had = next((d for d in users.list_devices() if d["id"] == device_id), None)
    users.revoke_device(device_id)
    if had is not None:
        audit.note(request, "device.revoke", "Forgot a trusted device", target=had["name"])
    return {"ok": True}


# ------------------------------------------------------- phones: one core device with its parts (#528)
def _phones_error(exc: devices.Refused) -> JSONResponse:
    return JSONResponse({"error": devices.REFUSALS[exc.key]}, status_code=404 if exc.key == "no_device" else 400)


def _phones(user_id: int | None) -> dict:
    return {"phones": [_iso(d) for d in devices.listing(user_id)]}


@app.get("/api/admin/phones", dependencies=[Depends(require("users.view"))])
async def admin_phones():
    """Every phone and computer, with the parts each holds (sign-in, intercom, check-in, push)."""
    return await asyncio.to_thread(_phones, None)


@app.put("/api/admin/phones/{phone_id}", dependencies=[Depends(require("users.manage"))])
async def admin_phone_rename(phone_id: int, payload: dict):
    try:
        await asyncio.to_thread(devices.rename, phone_id, payload.get("name"))
    except devices.Refused as exc:
        return _phones_error(exc)
    return await asyncio.to_thread(_phones, None)


@app.delete("/api/admin/phones/{phone_id}", dependencies=[Depends(require("users.manage"))])
async def admin_phone_revoke(phone_id: int, request: Request):
    """Forget a phone: its intercom session, push pages, check-in and sign-in trust all end."""
    gone = await asyncio.to_thread(devices.revoke, phone_id)
    if gone is None:
        return _phones_error(devices.Refused("no_device"))
    audit.note(request, "phone.revoke", "Forgot a phone: signed it out of sign-in, intercom, check-in and push",
               target=gone["name"], detail={"parts": gone["parts"]})
    return await asyncio.to_thread(_phones, None)


@app.post("/api/admin/phones/{phone_id}/merge", dependencies=[Depends(require("users.manage"))])
async def admin_phone_merge(phone_id: int, payload: dict):
    """Two entries that are the same phone: `into` keeps its name and takes the other's parts."""
    into = payload.get("into")
    if isinstance(into, bool) or not isinstance(into, int):
        return _phones_error(devices.Refused("no_device"))
    try:
        await asyncio.to_thread(devices.merge, into, phone_id)
    except devices.Refused as exc:
        return _phones_error(exc)
    return await asyncio.to_thread(_phones, None)


@app.get("/api/admin/me/phones", dependencies=[Depends(require(None, tokens=False))])
async def admin_me_phones(request: Request):
    return await asyncio.to_thread(_phones, request.state.user.id)


@app.put("/api/admin/me/phones/{phone_id}", dependencies=[Depends(require(None, tokens=False))])
async def admin_me_phone_rename(phone_id: int, request: Request, payload: dict):
    try:
        await asyncio.to_thread(lambda: devices.rename(phone_id, payload.get("name"), user_id=request.state.user.id))
    except devices.Refused as exc:
        return _phones_error(exc)
    return await asyncio.to_thread(_phones, request.state.user.id)


@app.delete("/api/admin/me/phones/{phone_id}", dependencies=[Depends(require(None, tokens=False))])
async def admin_me_phone_revoke(phone_id: int, request: Request):
    gone = await asyncio.to_thread(lambda: devices.revoke(phone_id, user_id=request.state.user.id))
    if gone is None:
        return _phones_error(devices.Refused("no_device"))
    audit.note(request, "phone.revoke", "Forgot one of their phones: signed it out of sign-in, intercom, check-in and push",
               target=gone["name"], detail={"parts": gone["parts"]})
    return await asyncio.to_thread(_phones, request.state.user.id)


# ------------------------------------------------------- the audit log (#172)
@app.get("/api/admin/audit", dependencies=[Depends(require("audit.view"))])
def admin_audit(who: str = "", kind: str = "", q: str = "", days: int = 0, before: int = 0):
    """The audit log, newest first, a page at a time (`before`: the last id shown). `who` is a user id or
    "system"; `kind` an action's area; `q` text to look for; `days` how far back."""
    if who == "system":
        actor = "system"
    elif who.isascii() and who.isdigit() and len(who) < 10:
        actor = int(who)
    else:
        actor = None
    found = audit.search(actor=actor, kind=kind[:64] or None, q=q.strip()[:100] or None,
                         days=days if 0 < days <= 3650 else None, before=before if before > 0 else None)
    return {**found, "people": audit.people(), "kinds": audit.kinds(), "keep_days": audit.KEEP_DAYS}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "running": engine.running}


# --------------------------------------------------------------- module routes (#157)
# Last, so a core route always wins. Every route a module adds answers 404 while the module is off.
for _mod in module_registry.all():
    for _router in _mod.routers():
        app.include_router(_router, dependencies=[Depends(module_registry.require_enabled(_mod.manifest.id))])
module_registry.on_change = engine._push_status
