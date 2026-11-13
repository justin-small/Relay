"""Relay -- FastAPI host.

Viewer pages are read-only and unauthenticated (venue LAN). The admin panel is
behind a token. The OpenAI key never leaves this process.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import socket
import threading
import time
from dataclasses import asdict
from datetime import datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from . import alerts as alertsmod
from . import announce as announcemod
from . import config, db, exporting, languages, netinfo, overlays, panelfw, panels, redact, schedules, serving, tls, users
from .alerts import alerts
from . import engine as engine_mod
from .engine import TRANSCRIPTION_STREAM, TRANSLATION_STREAM, demo_mode, engine
from .hub import hub
from . import modules as modules_pkg
from . import sidecar
from .modules import HERE as MODULES_DIR, modules as module_registry
from .panels import registry as panel_registry
from .recorder import recorder
from .scheduler import scheduler
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
LOGIN_LOCKOUT_S = 300.0
HEARTBEAT_S = 15.0
BLOCKLIST_POLL_S = 2.0

# The operator panel lives on its own port. Everything under these prefixes is
# refused on the viewer port, so a link handed to a room cannot reach the panel
# -- not even the login form.
ADMIN_PREFIXES = ("/admin", "/api/admin")


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
    "X-Frame-Options": "DENY",
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


def permissions_policy_for(path: str) -> str:
    return FLASH_PERMISSIONS_POLICY if path == FLASH_PATH else PERMISSIONS_POLICY


@app.middleware("http")
async def _permissions_policy(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Permissions-Policy", permissions_policy_for(request.url.path))
    return response


@app.middleware("http")
async def _split_admin_port(request: Request, call_next):
    if _is_admin_path(request.url.path) and not _on_admin_port(request):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return await call_next(request)


def _on_lan_change(ips) -> None:
    """The LAN address changed: re-mint the panel certificate if its names no longer fit, and tell the
    desktop shell (it pins the fingerprint and builds the viewer link) of both."""
    if tls.panel.refresh() and tls.panel.info:
        sidecar.emit("fingerprint", fingerprint=tls.panel.info.fingerprint)
    sidecar.emit("network", ips=list(ips))


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
    app.state.blocklist_task = asyncio.create_task(_watch_blocklist())
    app.state.alerts_task = asyncio.create_task(_alert_ticks())
    # A change of LAN address (Wi-Fi switch): mint a certificate that names the new address if the current
    # one does not; the admin page re-reads its viewer links and the fingerprint on its own.
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
            if await asyncio.to_thread(_any_users) is False:
                log.info("No users yet: open the operator panel on this computer to finish setting up Relay.")
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
    if db.available() and db.backup_due():
        db.backup()


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
        await asyncio.to_thread(db.close)


def _urls(port: int) -> list[str]:
    """The viewer links the panel shows (and turns into QR codes).

    Relay serves viewers itself, so the links are this machine's own network addresses (app/netinfo.py)
    on the viewer port, which is left out of the link when it is 80.
    """
    ips = netinfo.lan_ips() or ["127.0.0.1"]
    return [serving.link("http", ip, port) for ip in ips] + [serving.link("http", "127.0.0.1", port)]


def _serving_info() -> dict:
    """What the admin page shows about how Relay is reachable: ports, notices, panel links, fingerprint."""
    cfg = config.get()
    admin_port = serving.admin_port(cfg["admin_port"])
    ips = netinfo.lan_ips() or ["127.0.0.1"]
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
    and it caps a guesser on the venue LAN long before the lockout bites."""
    await asyncio.sleep(LOGIN_FAIL_DELAY_S)
    _login_failed(ip)


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
    "local_only": "Finish setting up Relay on the computer it runs on: open this page there.",
    "already_set_up": "Relay is already set up. Sign in instead.",
    "mismatch": "The two passwords do not match.",
    "bad_request": "That request is not valid.",
    "signin_required": "Sign in required",
    "not_allowed": "Not allowed",
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


def require(perm: str | None, *, pending_ok: bool = False):
    """FastAPI dependency factory: the request needs a signed-in user who holds `perm` (None: just signed in).

    401 {"detail": "Sign in required"} when not signed in; 403 "password_change_required" while the user must
    still change their password (unless `pending_ok`: only /me, the password change and logout); 403
    "Not allowed" when the role lacks the permission; 503 when the database is down. `x-admin-token` is
    honoured only while no account exists, and then acts as an Owner. It is a sync dependency, so the database
    calls run in the thread pool. `request.state.user` is the User.
    """
    def dep(request: Request) -> User:
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
    return dep


# Modules' admin routes use the same checks, and their pages the same templates (they cannot import this file).
modules_pkg.permission_hook = require
modules_pkg.templates = templates

# What a refusal from users.py means as a status. The text sent is always Refused.message.
_REFUSAL_STATUS = {
    "bad_login": 401, "wrong_pin": 401, "pin_cleared": 401, "no_device": 401,
    "disabled": 403, "role_too_high": 403, "account_above_you": 403, "self_action": 403, "wrong_password": 403,
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
                 finals: int = 0, type: str | None = None):
    if stream == ALERT_STREAM:
        return _alert_sender_stream()
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
        return panel_registry.checkin(body, peer)
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
# operator's choice. alerts.py says what bounds them.
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


@app.get("/alert", response_class=HTMLResponse)
async def page_alert(request: Request):
    """The musicians' page: one button per alert. Not on the chooser page; the operator hands out the link."""
    return templates.TemplateResponse(request, "alert.html", _viewer_ctx(request))


@app.post("/api/alerts/{button_id}/send")
async def alert_send(button_id: str, request: Request):
    """Flash a button's screens, or stop the alert it has flashing. `from` names the sender; a panel sends its
    `panel` id instead, and is named as Relay names it."""
    body = await _alert_body(request)
    sender = alertsmod.clean_name(body.get("from"))
    pid = body.get("panel")
    if pid is not None:
        if not panels.valid_id(pid) or not panel_registry.known(pid):
            raise HTTPException(404, "unknown panel")
        sender = panel_registry.display_name(pid)
    try:
        return {"ok": True, **alerts.press(button_id, sender)}
    except alertsmod.Refused as exc:
        return _alert_refused(exc)


@app.post("/api/alerts/{button_id}/cancel")
async def alert_cancel(button_id: str):
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
        return alerts.ack(body.get("id"), panel=panel, screen=screen)
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
                pw: bool = False, pin_user: int | None = None, setup: dict | None = None) -> HTMLResponse:
    """The sign-in form, the first-run form (no accounts yet) or, on a trusted device with PIN users, the PIN
    picker. `pw` forces the password form. Blocks (database). Passwords are never echoed back; a username is."""
    if setup is not None or not _any_users():
        form = {"username": "", "name": ""} | (setup or {})
        token_set = bool((config.get().get("admin_token") or "").strip())
        return _render(request, "login.html", {"mode": "setup", "error": error, **form, "token_set": token_set,
                                               "remote": not token_set and not _setup_local(request)}, status)
    device = users.device_for(request.cookies.get(DEVICE_COOKIE) or "")
    pins = users.pin_users() if device else []
    mode = "pin" if pins and not pw else "signin"
    return _render(request, "login.html", {"mode": mode, "error": error, "username": username,
                                           "pin_users": pins, "pin_user": pin_user,
                                           "device_name": device["name"] if device else ""}, status)


def _password_page(request: Request, *, error: str | None = None, status: int = 200) -> HTMLResponse:
    return _render(request, "password_change.html", {"error": error}, status)


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


def _me_payload(request: Request, user: User) -> dict:
    """Who is signed in and what they may do: GET /api/admin/me, and `me` on the admin page."""
    device = users.device_for(request.cookies.get(DEVICE_COOKIE) or "")
    return {
        "user": {"id": user.id, "username": user.username, "name": user.name, "role": user.role,
                 "must_change_password": user.must_change_password},
        "permissions": sorted(users.role_permissions(user.role)),
        "pin_set": user.pin_set,
        "device": {"trusted": device is not None, "name": device["name"] if device else None},
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, pw: str = ""):
    got = _page_gate(request, pw=pw == "1")
    if not isinstance(got, User):
        return got
    return templates.TemplateResponse(request, "admin.html", {
        "me": _me_payload(request, got),
        "module_nav": module_registry.admin_nav(got),
        "module_scripts": module_registry.admin_scripts(got),
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


def _signed_in(request: Request, token: str, max_age: int) -> RedirectResponse:
    resp = RedirectResponse("/admin", status_code=303)
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
            elif not _setup_local(request):
                raise _WebRefused("local_only")
            if password != confirm:
                raise _WebRefused("mismatch")
            user = users.create(username, name, "owner", password, temporary=False, actor=None)
            if api_key.strip():
                config.save({"openai_api_key": api_key.strip()[:300]})
            # Setup signs the Owner in without a password check, so stamp the sign-in here.
            db.execute("update users set last_login = ? where id = ?", (db.now(), user.id))
            global _have_users
            _have_users = True
        return users.start_session(user, via="password")

    try:
        token_, age = await asyncio.to_thread(work)
    except _WebRefused as exc:
        if exc.key == "bad_token":
            await _fail(ip)
        if exc.key == "local_only":
            return await asyncio.to_thread(_login_page, request, error=exc.message, status=403)
        if exc.key == "already_set_up":
            return await asyncio.to_thread(_login_page, request, error=exc.message, status=403)
        return await asyncio.to_thread(_login_page, request, error=exc.message,
                                       status=401 if exc.key == "bad_token" else 400, setup=shown)
    except users.Refused as exc:
        return await asyncio.to_thread(_login_page, request, error=exc.message, status=_refusal_status(exc),
                                       setup=shown)
    _LOGIN_FAILS.pop(ip, None)
    return _signed_in(request, token_, age)


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
        user = users.authenticate(username, password)
        return users.start_session(user, via="password")

    try:
        token, age = await asyncio.to_thread(work)
    except users.Refused as exc:
        if exc.key == "bad_login":
            await _fail(ip)
        return await asyncio.to_thread(_login_page, request, error=exc.message, status=_refusal_status(exc),
                                       username=username, pw=True)
    global _have_users
    _have_users = True
    _LOGIN_FAILS.pop(ip, None)
    return _signed_in(request, token, age)


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
        user = users.authenticate_pin(uid, _field(pin, 16), device["id"] if device else None)
        return users.start_session(user, via="pin", device_id=device["id"])

    try:
        token, age = await asyncio.to_thread(work)
    except users.Refused as exc:
        if exc.key in ("wrong_pin", "pin_cleared"):
            await _fail(ip)
        # Without a trusted device there is no picker to go back to: the password form, with the sentence.
        return await asyncio.to_thread(_login_page, request, error=exc.message, status=_refusal_status(exc),
                                       pin_user=uid, pw=exc.key == "no_device")
    global _have_users
    _have_users = True
    _LOGIN_FAILS.pop(ip, None)
    return _signed_in(request, token, age)


@app.post("/admin/password")
async def admin_password(request: Request, current: str = Form(""), new: str = Form(""),
                         confirm: str = Form("")):
    """The signed-in user's own password: the form shown while a change is pending, and a plain change."""
    ip = _client_ip(request)
    left = _login_locked(ip)

    def work():
        user = _session_user(request)
        if user is None:
            return None
        if left:
            return _password_page(request, error=_locked_sentence(left), status=429)
        if new != confirm:
            return _password_page(request, error=WEB_MESSAGES["mismatch"], status=400)
        users.change_own_password(user, current, new, keep_token=_session_token(request))
        return "done"

    try:
        got = await asyncio.to_thread(work)
    except users.Refused as exc:
        if exc.key == "wrong_password":
            await _fail(ip)
        return await asyncio.to_thread(_password_page, request, error=exc.message, status=_refusal_status(exc))
    if got is None or got == "done":
        return RedirectResponse("/admin", status_code=303)
    return got


@app.post("/admin/logout")
async def admin_logout(request: Request):
    token = _session_token(request)
    if token:
        # Ends it server-side, so a copied cookie is dead too. If the database is down the browser still
        # forgets its cookie.
        with contextlib.suppress(db.Unavailable):
            await asyncio.to_thread(users.end_session, token)
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
    from .audio import list_input_devices, selection_notice

    # Cached read: opening the panel must never re-initialise PortAudio, which
    # would close a live capture stream out from under the event.
    return {
        "config": config.redacted(),
        "status": engine.status(),
        "devices": list_input_devices(),
        "device_notice": selection_notice(config.get().get("audio_device")),
        "urls": _urls(serving.viewer_port(config.get()["port"])),
        "serving": _serving_info(),
    }


# --------------------------------------------------------------- modules (#157)
@app.get("/api/admin/modules", dependencies=[Depends(require("modules.view"))])
async def admin_modules():
    return {"modules": [module_registry.describe(m) for m in module_registry.all()]}


@app.post("/api/admin/modules/{mid}", dependencies=[Depends(require("modules.manage"))])
async def admin_module_switch(mid: str, payload: dict):
    """Switch a module on or off. Its pages appear or go once the admin page reloads."""
    if module_registry.get(mid) is None:
        raise HTTPException(404, "no such module")
    on = payload.get("enabled")
    if not isinstance(on, bool):
        return JSONResponse({"error": "enabled must be true or false"}, status_code=400)
    await module_registry.set_enabled(mid, on)
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


@app.get("/api/admin/devices", dependencies=[Depends(require("session.view"))])
async def admin_devices():
    from .audio import capture_is_live, list_input_devices, selection_notice

    live = capture_is_live()
    return {
        "devices": list_input_devices(refresh=True),
        "device_notice": selection_notice(config.get().get("audio_device")),
        "full_rescan": not live,
        "note": RESCAN_LIVE_NOTE if live else None,
    }


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
    "input_channel": "audio.manage",
    "source_language": "audio.manage",
    "default_font_px": "settings.manage",
    "speaking_indicator_vad": "settings.manage",
    "history_lines": "settings.manage",
    "realtime": "settings.manage",
    "openai_api_key": "credentials.manage",
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
        "input_channel",
        "source_language",
        "default_font_px",
        "speaking_indicator_vad",
        "history_lines",
    )
    for field in allowed_scalars:
        if field in payload:
            updates[field] = payload[field]

    key = payload.get("openai_api_key")
    if isinstance(key, str) and key.strip():
        updates["openai_api_key"] = key.strip()

    if isinstance(payload.get("realtime"), dict):
        updates["realtime"] = payload["realtime"]

    if isinstance(payload.get("recording"), dict):
        updates["recording"] = payload["recording"]

    cfg = config.save(updates)
    hub.history_lines = cfg["history_lines"]
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
        for f in ("audio_device", "input_channel", "source_language", "realtime")
    )
    if needs_restart and engine.running:
        await engine.restart()

    body = {"config": config.redacted(), "status": engine.status()}
    if blocklist_report is not None:
        body["blocklist_report"] = blocklist_report
    return JSONResponse(body)


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
    followed = panel_registry.followed_service_types()
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


async def _save_schedules(items: list[dict]) -> dict:
    config.save({"schedules": items})
    await scheduler.poke()
    engine._push_status()
    return scheduler.listing()


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
    return await _save_schedules(items)


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
    return await _save_schedules(items)


@app.delete("/api/admin/schedules/{sid}", dependencies=[Depends(require("schedules.manage"))])
async def admin_schedule_delete(sid: str):
    items = list(config.get()["schedules"])
    kept = [s for s in items if s["id"] != sid]
    if len(kept) == len(items):
        raise HTTPException(404, "unknown schedule")
    return await _save_schedules(kept)


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


@app.post("/api/admin/me/password", dependencies=[Depends(require(None, pending_ok=True))])
def admin_me_password(request: Request, payload: dict):
    """Change the signed-in user's own password. Their other sessions end; this browser's stays."""
    users.change_own_password(request.state.user, payload.get("current"), payload.get("new"),
                              keep_token=_session_token(request))
    return {"ok": True}


@app.post("/api/admin/me/pin", dependencies=[Depends(require(None))])
def admin_me_pin(request: Request, payload: dict):
    user: User = request.state.user
    users.set_pin(user, payload.get("password"), payload.get("pin"))
    return {"ok": True, "pin_set": True}


@app.delete("/api/admin/me/pin", dependencies=[Depends(require(None))])
def admin_me_pin_clear(request: Request):
    users.clear_pin(request.state.user.id)
    return {"ok": True, "pin_set": False}


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
    return {"user": asdict(made)}


@app.put("/api/admin/users/{user_id}", dependencies=[Depends(require("users.manage"))])
def admin_user_update(user_id: int, request: Request, payload: dict):
    changes = {k: payload[k] for k in ("name", "role", "disabled") if k in payload}
    if "disabled" in changes and not isinstance(changes["disabled"], bool):
        raise _bad_request()
    return {"user": asdict(users.update(user_id, actor=request.state.user, **changes))}


@app.post("/api/admin/users/{user_id}/password", dependencies=[Depends(require("users.manage"))])
def admin_user_password(user_id: int, request: Request, payload: dict):
    """Reset a password (a forgotten one): temporary, so they change it at next sign-in. Ends their
    sessions and clears their PIN."""
    users.set_password(user_id, payload.get("password"), temporary=True, actor=request.state.user)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/signout", dependencies=[Depends(require("users.manage"))])
def admin_user_signout(user_id: int, request: Request):
    target = users.get(user_id)
    if target is None:
        raise users.Refused("not_found")
    if users.ROLES.index(target.role) > users.ROLES.index(request.state.user.role):
        raise users.Refused("account_above_you")
    users.end_sessions(user_id)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}", dependencies=[Depends(require("users.manage"))])
def admin_user_delete(user_id: int, request: Request):
    users.delete(user_id, actor=request.state.user)
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
    token, row = users.trust_device(payload.get("name"), user)
    resp = JSONResponse({"device": _iso(row), "this": row["id"]})
    _set_cookie(resp, request, DEVICE_COOKIE, token, DEVICE_COOKIE_MAX_AGE)
    return resp


@app.delete("/api/admin/trusted-devices/{device_id}", dependencies=[Depends(require("users.manage"))])
def admin_device_revoke(device_id: int):
    users.revoke_device(device_id)
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "running": engine.running}


# --------------------------------------------------------------- module routes (#157)
# Last, so a core route always wins. Every route a module adds answers 404 while the module is off.
for _mod in module_registry.all():
    for _router in _mod.routers():
        app.include_router(_router, dependencies=[Depends(module_registry.require_enabled(_mod.manifest.id))])
module_registry.on_change = engine._push_status
