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
import time

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import alerts as alertsmod
from . import config, exporting, languages, panelfw, panels, planningcenter, redact, schedules
from .alerts import alerts
from .engine import TRANSCRIPTION_STREAM, TRANSLATION_STREAM, demo_mode, engine
from .hub import hub
from .panels import registry as panel_registry
from .pclive import follower as pc_follower
from .servicefeed import feed as service_feed
from . import pccontrol, propresenter
from .ppcontrol import control as pp_control
from .propresenter import follower as pp_follower, lyrics_feed
from .recorder import recorder
from .scheduler import scheduler

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


app.mount("/static", RevalidatedStaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _static_url(path: str) -> str:
    """A /static URL that changes whenever the file does, so a browser holding a copy cached before
    no-cache was sent (or before an update) fetches the new one instead of reusing the old."""
    try:
        version = format((BASE / "static" / path).stat().st_mtime_ns, "x")
    except OSError:
        return "/static/" + path
    return f"/static/{path}?v={version}"


templates.env.globals["static"] = _static_url

ADMIN_COOKIE = "relay_admin"
SESSION_TTL_S = 60 * 60 * 12
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
    """True unless this request arrived on the viewer port of a split setup.

    Only the viewer port is gated, deliberately: anything else -- a test client,
    a reverse proxy, `admin_port == port` -- keeps the old single-port
    behaviour rather than locking the operator out.
    """
    cfg = config.get()
    viewer_port = int(cfg.get("port") or 8000)
    admin_port = int(cfg.get("admin_port") or viewer_port)
    if admin_port == viewer_port:
        return True
    return _request_port(request) != viewer_port


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


async def _startup() -> None:
    config.ensure_file()
    cfg = config.load()
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
    # First tick runs straight away, so a relay started mid-window catches up.
    scheduler.start()
    # Idle until a Planning Center token is saved and a service type followed.
    pc_follower.start()
    # Idle until an address is entered and ProPresenter is switched on in admin.
    pp_follower.start()
    if not (cfg.get("admin_token") or "").strip():
        # _token_ok() returns False on an empty stored token, so the panel is
        # locked rather than open -- but an operator who cannot log in deserves
        # to know why. Run setup.command / setup.bat to set one.
        log.warning(
            "Admin token is not set: the operator panel will reject every login. "
            "Run setup.command (macOS) or setup.bat (Windows) to set one."
        )
    # Both sockets are loopback-only behind the Caddy front end, so these are
    # the internal addresses, not the ones anyone dials. The URLs the operator
    # and the room actually use are printed by tools/setup_caddy.py at startup,
    # where the published ports are known.
    log.info("Viewer socket: 127.0.0.1:%d", int(cfg["port"]))
    if int(cfg["admin_port"]) != int(cfg["port"]):
        admin_host = str(cfg.get("admin_host") or "127.0.0.1")
        if admin_host in _LOOPBACK_HOSTS:
            log.info("Panel socket: 127.0.0.1:%d (loopback only, HTTPS via the "
                     "front end)", int(cfg["admin_port"]))
        else:
            # Only reachable by hand-editing admin_host. Say plainly what it
            # costs: the panel reads and writes the OpenAI key and the admin
            # token, and on this socket they cross the network in the clear.
            log.warning(
                "Panel socket: %s:%d -- NOT loopback, so the panel is reachable "
                "in CLEARTEXT from the network. Set \"admin_host\" back to "
                "\"127.0.0.1\" in config.json and reach it over HTTPS instead.",
                admin_host,
                int(cfg["admin_port"]),
            )
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


async def _shutdown() -> None:
    scheduler.cancel()
    pc_follower.cancel()
    pp_follower.cancel()
    for name in ("demo_task", "blocklist_task", "alerts_task"):
        task = getattr(app.state, name, None)
        if task is not None:
            task.cancel()
    with contextlib.suppress(Exception):
        await engine.stop()


def _urls(port: int) -> list[str]:
    """The viewer links the panel shows (and turns into QR codes).

    In the Docker image the relay sits behind Caddy, so neither what this
    process can see nor its own port is what a phone should dial: the address
    is the container's bridge (172.x) and 8000 is not published. There the
    launcher hands in the host's LAN address (RELAY_ADMIN_IPS) and the image
    names the port the host publishes (RELAY_PUBLIC_HTTP) -- the same pair the
    startup banner prints. Run natively, the relay serves viewers itself.
    """
    public = os.environ.get("RELAY_PUBLIC_HTTP")
    if public:
        ips = [ip.strip() for ip in
               (os.environ.get("RELAY_ADMIN_IPS") or "").replace(";", ",").split(",")
               if ip.strip()]
        suffix = "" if int(public) == 80 else f":{int(public)}"
        return [f"http://{ip}{suffix}/" for ip in ips] + [f"http://127.0.0.1{suffix}/"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return [f"http://{ip}:{port}/", f"http://127.0.0.1:{port}/"]


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


# Session ids -> expiry. In-process only, so every restart logs everyone out
# and the admin token itself never travels in a cookie.
_SESSIONS: dict[str, float] = {}

# Per-client-IP login failures: ip -> (count, last failure time).
_LOGIN_FAILS: dict[str, tuple[int, float]] = {}


def _new_session() -> str:
    now = time.monotonic()
    for sid, exp in [kv for kv in _SESSIONS.items() if kv[1] <= now]:
        _SESSIONS.pop(sid, None)
    sid = secrets.token_urlsafe(32)
    _SESSIONS[sid] = now + SESSION_TTL_S
    return sid


def _session_ok(request: Request) -> bool:
    """True when the cookie carries a live session id.

    The cookie is an opaque random id, never the admin token, so a sniffed
    cookie on venue Wi-Fi cannot be replayed past a restart and cannot be
    turned back into the token the operator typed.
    """
    sid = request.cookies.get(ADMIN_COOKIE) or ""
    exp = _SESSIONS.get(sid)
    if exp is None:
        return False
    if exp <= time.monotonic():
        _SESSIONS.pop(sid, None)
        return False
    return True


def _drop_session(request: Request) -> None:
    _SESSIONS.pop(request.cookies.get(ADMIN_COOKIE) or "", None)


def _set_session_cookie(resp: Response, request: Request | None = None) -> None:
    # `Secure` only when this request actually came over TLS: setting it on a
    # plain-HTTP login would have the browser drop the cookie and lock the
    # operator out of a perfectly good loopback or single-port setup.
    secure = bool(request is not None and _is_https(request))
    resp.set_cookie(
        ADMIN_COOKIE,
        _new_session(),
        httponly=True,
        samesite="strict",
        secure=secure,
        max_age=SESSION_TTL_S,
        path="/",
    )


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

    Behind the Caddy front end every request has a peer of 127.0.0.1, which
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

    Direct requests are plain HTTP. Through the front end the app still speaks
    HTTP over loopback, so the only honest signal is Caddy's X-Forwarded-Proto.
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


def require_admin(request: Request) -> bool:
    if _session_ok(request):
        return True
    token = (config.get().get("admin_token") or "").strip()
    if _token_ok(request.headers.get("x-admin-token") or "", token):
        return True
    raise HTTPException(status_code=401, detail="Admin token required")


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
        # ?lyrics=1: ProPresenter's lyrics in place of the captions while a song is up (propresenter.py).
        "lyrics_for": "screen" if qp.get("lyrics") in ("1", "true", "yes") else "",
    }
    if font_override:
        ctx["font_px"] = font_override
    return templates.TemplateResponse(request, "viewer.html", ctx)


import re as _re

# A background colour written verbatim into a CSS custom property. Only three
# shapes are accepted -- the transparent keyword, a hex colour, or a bare colour
# name -- so a hand-typed ?bg= cannot break out of the value and inject CSS.
_BG_HEX = _re.compile(r"#[0-9A-Fa-f]{3,8}\Z")
_BG_NAME = _re.compile(r"[A-Za-z]{1,20}\Z")


def _overlay_bg(raw: str) -> str:
    if raw == "transparent" or _BG_HEX.match(raw) or _BG_NAME.match(raw):
        return raw
    return "transparent"


@app.get("/overlay", response_class=HTMLResponse)
async def page_overlay(request: Request):
    """Broadcast overlay: a transparent, fixed-canvas, caret-free caption feed
    meant to be composited over live video -- an OBS/ProPresenter browser source,
    or keyed over program on an ATEM. Reuses viewer.html; the
    template branches on `overlay` for the transparent, hold-to-hide, plated
    layout. Not on the chooser page -- this is switcher setup, not an audience
    choice. Everything is set through the URL because it is pasted into another
    application's address field once."""
    qp = request.query_params

    stream = qp.get("stream", "translation")
    if stream not in ("transcription", "translation", "both"):
        stream = "translation"

    # Fixed target: an explicit ?lang= wins, else the first live target -- there
    # is nobody at a switcher to pick one (same resolution as /screen).
    fixed_lang = (qp.get("lang") or "").upper()
    if fixed_lang not in languages.TARGETS:
        live = engine.live_targets()
        fixed_lang = live[0]["target"] if live else ""
    overlay_iso = languages.target_iso(fixed_lang) if fixed_lang else ""

    def _clamp_int(name: str, default: int, lo: int, hi: int | None = None) -> int:
        try:
            val = int(qp.get(name, str(default)))
        except (TypeError, ValueError):
            return default
        val = max(lo, val)
        return val if hi is None else min(hi, val)

    def _clamp_float(name: str, default: float, lo: float, hi: float) -> float:
        try:
            val = float(qp.get(name, str(default)))
        except (TypeError, ValueError):
            return default
        return min(hi, max(lo, val))

    lines = _clamp_int("lines", 2, 1, 12)
    font = _clamp_int("font", 54, 8, 400)
    width = _clamp_int("w", 1920, 16, 8192)
    height = _clamp_int("h", 1080, 16, 8192)
    hold = _clamp_int("hold", 4000, 0, 600_000)
    safe = _clamp_float("safe", 5.0, 0.0, 45.0)
    plate = _clamp_float("plate", 0.55, 0.0, 1.0)
    matte = qp.get("matte") in ("1", "true", "yes")
    bg = _overlay_bg(qp.get("bg", "transparent"))

    ctx = _viewer_ctx(request) | {
        "mode": stream,
        "overlay": True,
        "overlay_lang": fixed_lang,
        "overlay_iso": overlay_iso,
        "overlay_lines": lines,
        "overlay_font": font,
        "overlay_w": width,
        "overlay_h": height,
        "overlay_hold": hold,
        # Title-safe inset resolved to px on the fixed canvas, so the layout does
        # not depend on the browser-source window matching the canvas size.
        "overlay_pad_x": round(width * safe / 100),
        "overlay_pad_y": round(height * safe / 100),
        "overlay_plate": plate,
        "overlay_bg": bg,
        "overlay_matte": matte,
        "lyrics_for": "overlay" if qp.get("lyrics") in ("1", "true", "yes") else "",
    }
    return templates.TemplateResponse(request, "viewer.html", ctx)


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


SERVICE_STREAM = "service"


def _service_stream(st: str) -> StreamingResponse:
    """Today's plan and LIVE state for one followed service type (servicefeed.py)."""
    queue = service_feed.subscribe(st)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield _sse(service_feed.tick_payload(st)).encode()
                    continue
                yield frame
        finally:
            service_feed.unsubscribe(st, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


LYRICS_STREAM = "lyrics"


def _lyrics_stream(audience: str) -> StreamingResponse:
    """ProPresenter's current slide as lyrics, whole in each message (propresenter.LyricsFeed). Repeated at each
    heartbeat, so a panel's silence timer never fires on a quiet stream."""
    queue = lyrics_feed.subscribe(audience)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield alertsmod.sse_frame(lyrics_feed.payload(audience))
                    continue
                yield frame
        finally:
            lyrics_feed.unsubscribe(audience, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


ALERT_STREAM = "alert"


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
    if stream == LYRICS_STREAM:
        audience = request.query_params.get("for") or "viewer"
        if audience not in propresenter.AUDIENCES:
            raise HTTPException(400, "unknown audience")
        # Licensed lyrics: each audience is the operator's switch, and the phone viewer's is off by default.
        if not lyrics_feed.allowed(audience):
            raise HTTPException(403, "the lyrics feed is not switched on for this audience")
        return _lyrics_stream(audience)
    if stream == SERVICE_STREAM:
        st = (type or "").strip()
        # Only a followed service type: the feed is no way to make Relay ask
        # Planning Center about anything else.
        if not any(o["service_type"] == st for o in service_feed.offered()):
            raise HTTPException(404, "not a followed service type")
        return _service_stream(st)
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
async def _read_limited(request: Request, limit: int) -> bytes:
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


panel_control = pccontrol.Control(pc_follower, panel_registry, service_feed)


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
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)


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
        raise HTTPException(exc.status, str(exc))

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


@app.post("/api/panels/{panel_id}/service/{action}")
async def panel_service_action(panel_id: str, action: str, request: Request):
    """Next / Previous / Take control / Release from a panel the operator lets
    control, with its key (pccontrol.py). Answers with the new Service feed
    state, or {"ok": false, "error": <code>} for the panel to word."""
    if action not in pccontrol.ACTIONS:
        raise HTTPException(404, "unknown action")
    if not panels.valid_id(panel_id) or not panel_registry.known(panel_id):
        raise HTTPException(404, "unknown panel")
    raw = await _read_limited(request, pccontrol.BODY_MAX_BYTES)
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    try:
        state = await panel_control.press(panel_id, action, body)
    except pccontrol.Refused as exc:
        return JSONResponse({"ok": False, "error": exc.code}, status_code=exc.status)
    return {"ok": True, "service": state}


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
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not _session_ok(request):
        return templates.TemplateResponse(
            request, "login.html", {"error": None}, status_code=200
        )
    return templates.TemplateResponse(request, "admin.html")


@app.get("/admin/flash", response_class=HTMLResponse)
async def admin_flash_page(request: Request, board: str = ""):
    """Write a new or recovered panel's whole firmware over USB from the browser (copied from VXP's
    board flasher). The page holds every sentence it can show; `flasher.js` picks which is visible."""
    if not _session_ok(request):
        return templates.TemplateResponse(request, "login.html", {"error": None}, status_code=200)
    boards = panelfw.active_boards()
    chosen = next((b for b in boards if b.id == board), boards[0])
    info = panelfw.current_flash(chosen.id)
    return templates.TemplateResponse(request, "flash.html", {
        "boards": [{"id": b.id, "label": b.label, "flash": panelfw.current_flash(b.id)} for b in boards],
        "board": chosen, "flash_mb": panelfw.FLASH_MB.get(chosen.id, 16),
        "project": panelfw.PROJECT_NAME, "flash": info,
    })


@app.post("/admin/login")
async def admin_login(request: Request, token: str = Form(...)):
    ip = _client_ip(request)
    left = _login_locked(ip)
    if left:
        log.warning("Admin login from %s refused, locked for %ds", ip, int(left))
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": f"Too many attempts. Try again in {int(left) // 60 + 1} min."},
            status_code=429,
        )

    real = (config.get().get("admin_token") or "").strip()
    if not _token_ok(token.strip(), real):
        # A fixed delay on every failure: it costs an operator who mistyped
        # under a second, and caps a guesser on the venue LAN long before the
        # lockout bites.
        await asyncio.sleep(LOGIN_FAIL_DELAY_S)
        _login_failed(ip)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect token."}, status_code=401
        )

    _LOGIN_FAILS.pop(ip, None)
    resp = RedirectResponse("/admin", status_code=303)
    _set_session_cookie(resp, request)
    return resp


@app.post("/admin/logout")
async def admin_logout(request: Request):
    _drop_session(request)
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(ADMIN_COOKIE, path="/")
    return resp


RESCAN_LIVE_NOTE = (
    "Capture is running, so the audio backend was left alone \u2014 anything plugged in "
    "since start-up will not appear. Stop capture and rescan for a full list."
)


@app.get("/api/admin/state", dependencies=[Depends(require_admin)])
async def admin_state():
    from .audio import list_input_devices

    # Cached read: opening the panel must never re-initialise PortAudio, which
    # would close a live capture stream out from under the event.
    return {
        "config": config.redacted(),
        "status": engine.status(),
        "devices": list_input_devices(),
        "urls": _urls(config.get()["port"]),
    }


@app.get("/api/admin/devices", dependencies=[Depends(require_admin)])
async def admin_devices():
    from .audio import capture_is_live, list_input_devices

    live = capture_is_live()
    return {
        "devices": list_input_devices(refresh=True),
        "full_rescan": not live,
        "note": RESCAN_LIVE_NOTE if live else None,
    }


@app.get("/api/admin/status", dependencies=[Depends(require_admin)])
async def admin_status():
    return engine.status()


@app.get("/api/admin/status/stream", dependencies=[Depends(require_admin)])
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


@app.post("/api/admin/config", dependencies=[Depends(require_admin)])
async def admin_config(request: Request, payload: dict):
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

    admin_token = payload.get("admin_token")
    if isinstance(admin_token, str) and admin_token.strip():
        updates["admin_token"] = admin_token.strip()

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
    resp = JSONResponse(body)
    if "admin_token" in updates:
        # New token, so every session issued against the old one dies; the
        # operator who made the change keeps a fresh one.
        _SESSIONS.clear()
        _set_session_cookie(resp, request)
    return resp


def _pc_body() -> dict:
    return {"planning_center": config.redacted()["planning_center"]}


@app.post("/api/admin/planning-center", dependencies=[Depends(require_admin)])
async def admin_planning_center_save(payload: dict):
    """Save the Personal Access Token. A blank field keeps what is saved; a new
    ID or secret means a different token, so the followed service types reset.
    The secret is never in the response."""
    saved = config.get()["planning_center"]
    app_id = str(payload.get("app_id") or "").strip() or saved["app_id"]
    secret = str(payload.get("secret") or "").strip() or saved["secret"]
    if not app_id or not secret:
        return JSONResponse({"error": "Enter both the application ID and the secret."}, status_code=400)
    changed = (app_id, secret) != (saved["app_id"], saved["secret"])
    config.save({"planning_center": {
        "app_id": app_id,
        "secret": secret,
        "service_types": [] if changed else saved["service_types"],
        "plan_overrides": {} if changed else saved["plan_overrides"],
        "show_notes": saved["show_notes"],
    }}, replace=("planning_center",))
    log.info("Planning Center token %s", "saved" if changed else "unchanged")
    await pc_follower.poke()
    return _pc_body()


@app.post("/api/admin/planning-center/test", dependencies=[Depends(require_admin)])
async def admin_planning_center_test():
    """Try the saved token and list the account's service types."""
    client = planningcenter.client_from(config.get())
    if client is None:
        return JSONResponse({"error": "Save an application ID and secret first."}, status_code=400)
    try:
        types = await asyncio.get_running_loop().run_in_executor(None, client.service_types)
    except planningcenter.PlanningCenterError as exc:
        status = {"auth": 400, "rate": 429, "network": 502}.get(exc.kind, 502)
        return JSONResponse({"error": str(exc)}, status_code=status)
    return {**_pc_body(), "service_types": types}


@app.post("/api/admin/planning-center/service-types/refresh", dependencies=[Depends(require_admin)])
async def admin_planning_center_service_types_refresh():
    """The account's service types, read from Planning Center. The page calls this after Save
    and once a minute while the list is on screen; it changes nothing."""
    return await admin_planning_center_test()


@app.post("/api/admin/planning-center/service-types", dependencies=[Depends(require_admin)])
async def admin_planning_center_service_types(payload: dict):
    """Which service types Relay follows."""
    if not planningcenter.connected(config.get()):
        return JSONResponse({"error": "Connect to Planning Center first."}, status_code=400)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return JSONResponse({"error": "ids must be a list."}, status_code=400)
    saved = config.get()["planning_center"]
    config.save({"planning_center": {**saved, "service_types": ids}}, replace=("planning_center",))
    await pc_follower.poke()
    return _pc_body()


@app.post("/api/admin/planning-center/notes", dependencies=[Depends(require_admin)])
async def admin_planning_center_notes(payload: dict):
    """"Show item notes on panels". Off by default: notes can hold names and
    pastoral details, and the service feed is on the viewer port with no login."""
    show = payload.get("show")
    if not isinstance(show, bool):
        return JSONResponse({"error": "show must be true or false."}, status_code=400)
    saved = config.get()["planning_center"]
    config.save({"planning_center": {**saved, "show_notes": show}}, replace=("planning_center",))
    log.info("Planning Center item notes on panels: %s", "shown" if show else "hidden")
    service_feed.refresh()
    return _pc_body()


@app.get("/api/admin/planning-center/live", dependencies=[Depends(require_admin)])
async def admin_planning_center_live():
    """Today's plan and its LIVE state for each followed service type, and
    the presses made from panels."""
    return {**pc_follower.snapshot(), "actions": panel_control.recent()}


@app.post("/api/admin/planning-center/plan", dependencies=[Depends(require_admin)])
async def admin_planning_center_plan(payload: dict):
    """Follow a different plan today for one service type; a blank plan goes
    back to Relay's own pick. The choice lapses when the day ends."""
    saved = config.get()["planning_center"]
    st = str(payload.get("service_type") or "")
    if st not in saved["service_types"]:
        return JSONResponse({"error": "Relay is not following that service type."}, status_code=400)
    plan = str(payload.get("plan") or "").strip()
    if plan and not plan.isdigit():
        return JSONResponse({"error": "plan must be a Planning Center plan id."}, status_code=400)
    overrides = {k: v for k, v in saved["plan_overrides"].items() if k != st}
    if plan:
        track = pc_follower.tracks.get(st)
        day = track.day if track and track.day else planningcenter.local_day(
            scheduler.now(), pc_follower.zone or pc_follower.fallback_zone())
        overrides[st] = {"plan": plan, "day": day.isoformat()}
    config.save({"planning_center": {**saved, "plan_overrides": overrides}}, replace=("planning_center",))
    log.info("Planning Center, service type %s: operator %s", st,
             f"picked plan {plan} for today" if plan else "cleared the plan pick")
    await pc_follower.poke()
    return pc_follower.snapshot()


@app.delete("/api/admin/planning-center", dependencies=[Depends(require_admin)])
async def admin_planning_center_disconnect():
    config.save({"planning_center": {}}, replace=("planning_center",))
    log.info("Planning Center disconnected")
    await pc_follower.poke()
    return _pc_body()


# --------------------------------------------------------------- ProPresenter (#138)
def _pp_body() -> dict:
    s = config.get()["propresenter"]
    return {"settings": s, "state": pp_follower.state(), "lyrics_readers": lyrics_feed.viewer_count()}


_PP_ERRORS = {
    "view_only": "View-only mode is on, so Relay did not change anything in ProPresenter.",
    "timeout": "ProPresenter did not answer in time.",
    "network": "Could not reach ProPresenter. Check the address and port, and that ProPresenter is running.",
    "bad": "That address answered, but not like ProPresenter.",
}


def _pp_error(exc: propresenter.ProError) -> JSONResponse:
    """Answer with a fixed message chosen by the error's kind; the detail goes to the log only, so no
    exception text (socket errors, ProPresenter's own reply) reaches the browser."""
    log.warning("ProPresenter: %s", exc)
    status = {"view_only": 409, "http": 502, "bad": 502, "timeout": 504, "network": 502}.get(exc.kind, 502)
    if exc.kind == "http":
        message = ("ProPresenter said not found: is Network switched on in its preferences?" if exc.status == 404
                   else f"ProPresenter refused the request ({int(exc.status)}).")
    else:
        message = _PP_ERRORS.get(exc.kind, "ProPresenter could not do that.")
    return JSONResponse({"error": message}, status_code=status)


@app.get("/api/admin/propresenter", dependencies=[Depends(require_admin)])
async def admin_propresenter():
    return _pp_body()


@app.post("/api/admin/propresenter", dependencies=[Depends(require_admin)])
async def admin_propresenter_save(payload: dict):
    """Save the connection and switches. Only the keys sent change. A new address reconnects at once."""
    saved = config.get()["propresenter"]
    merged = {**saved, **{k: v for k, v in payload.items() if k in saved and k != "lyrics"}}
    if isinstance(payload.get("lyrics"), dict):
        merged["lyrics"] = {**saved["lyrics"], **payload["lyrics"]}
    if payload.get("host") and not propresenter.clean_host(payload["host"]):
        return JSONResponse({"error": "Enter just the machine's name or IP address, with no http:// or port."},
                            status_code=400)
    if payload.get("port") not in ("", None, 0) and "port" in payload and not propresenter.clean_port(payload["port"]):
        return JSONResponse({"error": "The port is a number from 1 to 65535."}, status_code=400)
    clean = propresenter.clean_stored(merged)
    if clean["enabled"] and not (clean["host"] and clean["port"]):
        return JSONResponse({"error": "Enter the machine's address and ProPresenter's port first. "
                                      "The port is in ProPresenter's Network preferences."}, status_code=400)
    config.save({"propresenter": clean}, replace=("propresenter",))
    log.info("ProPresenter settings saved (%s, view-only %s)",
             "on" if clean["enabled"] else "off", "on" if clean["view_only"] else "off")
    await pp_follower.poke()
    lyrics_feed.refresh()
    return _pp_body()


@app.post("/api/admin/propresenter/test", dependencies=[Depends(require_admin)])
async def admin_propresenter_test(payload: dict):
    """`GET /version` on the address in the form (saved or not). Changes nothing."""
    host = propresenter.clean_host(payload.get("host"))
    port = propresenter.clean_port(payload.get("port"))
    if not host or not port:
        return JSONResponse({"error": "Enter the machine's address and ProPresenter's port."}, status_code=400)
    client = propresenter.Client(host, port)
    try:
        info = await pp_follower.call(client.version)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return {"ok": True, "version": info}


@app.post("/api/admin/propresenter/discover", dependencies=[Depends(require_admin)])
async def admin_propresenter_discover():
    """ProPresenters announcing themselves on the LAN (mDNS). Nothing is saved or contacted."""
    return {"found": await pp_follower.call(propresenter.discover, 2.5)}


@app.post("/api/admin/propresenter/stage", dependencies=[Depends(require_admin)])
async def admin_propresenter_stage(payload: dict):
    text = pp_follower.clean_message(payload.get("text"))
    if not text:
        return JSONResponse({"error": "Type the message to show."}, status_code=400)
    try:
        await pp_follower.set_stage_message(text)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return _pp_body()


@app.delete("/api/admin/propresenter/stage", dependencies=[Depends(require_admin)])
async def admin_propresenter_stage_clear():
    try:
        await pp_follower.clear_stage_message()
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return _pp_body()


# --- ProPresenter control (#138 stage 2). Every route is admin-protected; the writes go through the one client,
# which refuses them in view-only mode (409). Errors answer through _pp_error or a fixed sentence.
async def _pp_run(fn, *args):
    try:
        data = await fn(*args)
    except PermissionError:
        return JSONResponse({"error": "That is not allowed by the operator-safe selection."}, status_code=403)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return data if data is not None else {"ok": True}


@app.get("/api/admin/propresenter/slides", dependencies=[Depends(require_admin)])
async def admin_pp_slides(uuid: str = ""):
    return await _pp_run(pp_control.slides, uuid or None)


@app.get("/api/admin/propresenter/thumbnail/{uuid}/{index}", dependencies=[Depends(require_admin)])
async def admin_pp_thumbnail(uuid: str, index: int, v: str = ""):
    """A slide thumbnail, proxied and cached by Relay (key: presentation, index, change marker `v`)."""
    try:
        ctype, data = await pp_control.thumbnail(uuid, index, v)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/admin/propresenter/step", dependencies=[Depends(require_admin)])
async def admin_pp_step(payload: dict):
    return await _pp_run(pp_control.step, payload.get("direction"))


@app.post("/api/admin/propresenter/next-presentation", dependencies=[Depends(require_admin)])
async def admin_pp_next_presentation():
    return await _pp_run(pp_control.next_presentation)


@app.post("/api/admin/propresenter/slide", dependencies=[Depends(require_admin)])
async def admin_pp_slide(payload: dict):
    return await _pp_run(pp_control.trigger_slide, payload.get("uuid"), payload.get("index"))


@app.post("/api/admin/propresenter/clear", dependencies=[Depends(require_admin)])
async def admin_pp_clear(payload: dict):
    return await _pp_run(pp_control.clear, payload.get("layer"))


@app.get("/api/admin/propresenter/playlists", dependencies=[Depends(require_admin)])
async def admin_pp_playlists():
    return await _pp_run(pp_control.playlists)


@app.get("/api/admin/propresenter/playlists/{playlist}", dependencies=[Depends(require_admin)])
async def admin_pp_playlist(playlist: str):
    return await _pp_run(pp_control.playlist_items, playlist)


@app.post("/api/admin/propresenter/playlists/{playlist}/trigger", dependencies=[Depends(require_admin)])
async def admin_pp_playlist_trigger(playlist: str, payload: dict):
    return await _pp_run(pp_control.trigger_playlist_item, playlist, payload.get("index"))


@app.get("/api/admin/propresenter/macros", dependencies=[Depends(require_admin)])
async def admin_pp_macros(all: bool = False):
    return await _pp_run(pp_control.macros, all)


@app.post("/api/admin/propresenter/macros/{macro}/trigger", dependencies=[Depends(require_admin)])
async def admin_pp_macro_trigger(macro: str):
    return await _pp_run(pp_control.trigger_macro, macro)


@app.get("/api/admin/propresenter/looks", dependencies=[Depends(require_admin)])
async def admin_pp_looks():
    return await _pp_run(pp_control.looks)


@app.post("/api/admin/propresenter/looks/{look}/trigger", dependencies=[Depends(require_admin)])
async def admin_pp_look_trigger(look: str):
    return await _pp_run(pp_control.trigger_look, look)


@app.get("/api/admin/propresenter/timers", dependencies=[Depends(require_admin)])
async def admin_pp_timers():
    return await _pp_run(pp_control.timers)


@app.post("/api/admin/propresenter/timers/{timer}/{op}", dependencies=[Depends(require_admin)])
async def admin_pp_timer(timer: str, op: str):
    return await _pp_run(pp_control.timer, timer, op)


@app.get("/api/admin/propresenter/messages", dependencies=[Depends(require_admin)])
async def admin_pp_messages():
    return await _pp_run(pp_control.messages)


@app.post("/api/admin/propresenter/messages/{message}/trigger", dependencies=[Depends(require_admin)])
async def admin_pp_message_trigger(message: str, payload: dict):
    return await _pp_run(pp_control.trigger_message, message, payload.get("tokens"))


@app.post("/api/admin/propresenter/messages/{message}/clear", dependencies=[Depends(require_admin)])
async def admin_pp_message_clear(message: str):
    return await _pp_run(pp_control.clear_message, message)


@app.post("/api/admin/propresenter/transport", dependencies=[Depends(require_admin)])
async def admin_pp_transport(payload: dict):
    return await _pp_run(pp_control.go_to_time, payload.get("seconds"))


@app.post("/api/admin/target/{target}", dependencies=[Depends(require_admin)])
async def admin_target(target: str, payload: dict):
    target = target.upper()
    try:
        status = await engine.set_target(
            target, bool(payload.get("enabled"))
        )
    except KeyError:
        raise HTTPException(404, "unknown target")
    return status


@app.post("/api/admin/start", dependencies=[Depends(require_admin)])
async def admin_start():
    scheduler.manual_start()
    try:
        return await engine.start()
    except RuntimeError as exc:
        # The refusal last: status() carries its own "error" (the sticky
        # last_error), which must not mask why this press failed.
        return JSONResponse({**engine.status(), "error": str(exc)}, status_code=400)


@app.post("/api/admin/stop", dependencies=[Depends(require_admin)])
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
alerts.on_stage = lambda alert_id, text: (pp_follower.alert_ended(alert_id) if text is None
                                          else pp_follower.alert_started(alert_id, text))


@app.get("/api/admin/alerts", dependencies=[Depends(require_admin)])
async def admin_alerts():
    return _alerts_frame()


@app.post("/api/admin/alerts", dependencies=[Depends(require_admin)])
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


@app.get("/api/admin/panels", dependencies=[Depends(require_admin)])
async def admin_panels():
    return _panels_frame()


# Firmware for the panels. These are declared before `/api/admin/panels/{panel_id}`, which would
# otherwise take "firmware" for a panel's id.
@app.get("/api/admin/panels/firmware", dependencies=[Depends(require_admin)])
async def admin_panel_firmware(board: str = ""):
    info = panelfw.current(_board_arg(board))
    return {"firmware": info.public() if info else None, "boards": _firmware_boards()}


@app.post("/api/admin/panels/firmware", dependencies=[Depends(require_admin)])
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


@app.delete("/api/admin/panels/firmware", dependencies=[Depends(require_admin)])
async def admin_panel_firmware_delete(board: str = ""):
    board = _board_arg(board)
    had = await asyncio.to_thread(panelfw.remove, board)
    panel_registry.firmware_changed(board)
    return {"ok": True, "removed": had}


@app.post("/api/admin/panels/firmware/update", dependencies=[Depends(require_admin)])
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
@app.get("/api/admin/panels/flash", dependencies=[Depends(require_admin)])
async def admin_panel_flash(board: str = ""):
    info = panelfw.current_flash(_board_arg(board))
    return {"flash": info.public() if info else None}


@app.post("/api/admin/panels/flash", dependencies=[Depends(require_admin)])
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


@app.delete("/api/admin/panels/flash", dependencies=[Depends(require_admin)])
async def admin_panel_flash_delete(board: str = ""):
    had = await asyncio.to_thread(panelfw.remove_flash, _board_arg(board))
    return {"ok": True, "removed": had}


@app.get("/api/admin/panels/flash/{name}", dependencies=[Depends(require_admin)])
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


@app.post("/api/admin/panels/{panel_id}", dependencies=[Depends(require_admin)])
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
    followed = config.get()["planning_center"]["service_types"]
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
    try:
        return panel_registry.update(panel_id, **kwargs)
    except KeyError:
        raise HTTPException(404, "unknown panel")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/admin/panels/{panel_id}/reboot", dependencies=[Depends(require_admin)])
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


@app.delete("/api/admin/panels/{panel_id}", dependencies=[Depends(require_admin)])
async def admin_panel_forget(panel_id: str):
    if not panel_registry.forget(panel_id):
        raise HTTPException(404, "unknown panel")
    return {"ok": True}


def _schedule_error(exc: ValueError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


async def _save_schedules(items: list[dict]) -> dict:
    config.save({"schedules": items})
    await scheduler.poke()
    engine._push_status()
    return scheduler.listing()


@app.get("/api/admin/schedules", dependencies=[Depends(require_admin)])
async def admin_schedules():
    return scheduler.listing()


@app.post("/api/admin/schedules", dependencies=[Depends(require_admin)])
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


@app.put("/api/admin/schedules/{sid}", dependencies=[Depends(require_admin)])
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


@app.delete("/api/admin/schedules/{sid}", dependencies=[Depends(require_admin)])
async def admin_schedule_delete(sid: str):
    items = list(config.get()["schedules"])
    kept = [s for s in items if s["id"] != sid]
    if len(kept) == len(items):
        raise HTTPException(404, "unknown schedule")
    return await _save_schedules(kept)


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


@app.get("/api/admin/recordings", dependencies=[Depends(require_admin)])
async def admin_recordings():
    cfg = config.get()
    return {
        "enabled": cfg["recording"]["enabled"],
        "keep_runs": cfg["recording"]["keep_runs"],
        "dir": str(config.recordings_path()),
        "runs": recorder.list_runs(),
    }


@app.get("/api/admin/recordings/{run_id}", dependencies=[Depends(require_admin)])
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
         dependencies=[Depends(require_admin)])
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
         dependencies=[Depends(require_admin)])
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


@app.delete("/api/admin/recordings/{run_id}", dependencies=[Depends(require_admin)])
async def admin_recording_delete(run_id: str):
    _run_or_404(run_id)
    if not recorder.delete_run(run_id):
        raise HTTPException(409, "that run is still being recorded")
    return {"deleted": run_id, "runs": recorder.list_runs()}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "running": engine.running}
