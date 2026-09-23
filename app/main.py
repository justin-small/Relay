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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, exporting, languages, redact, schedules
from .engine import TRANSCRIPTION_STREAM, TRANSLATION_STREAM, demo_mode, engine
from .hub import hub
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
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

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
    # First tick runs straight away, so a relay started mid-window catches up.
    scheduler.start()
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


async def _shutdown() -> None:
    scheduler.cancel()
    for name in ("demo_task", "blocklist_task"):
        task = getattr(app.state, name, None)
        if task is not None:
            task.cancel()
    with contextlib.suppress(Exception):
        await engine.stop()


def _urls(port: int) -> list[str]:
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

    ctx = _viewer_ctx(request) | {
        "mode": stream,
        "screen": True,
        "screen_lang": fixed_lang,
        "screen_iso": screen_iso,
        "screen_lines": lines,
        "screen_align": align,
        "screen_font": font_override,
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


@app.get("/stream")
async def stream(request: Request, stream: str = "translation", lang: str | None = None):
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
    channel, queue = await hub.subscribe(stream, lang, last_id)

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


# --------------------------------------------------------------- admin
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not _session_ok(request):
        return templates.TemplateResponse(
            request, "login.html", {"error": None}, status_code=200
        )
    return templates.TemplateResponse(request, "admin.html")


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
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    msg = engine.meter()
                yield _sse(msg)
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

    if "blocklist" in payload:
        # The file is the source of truth; the panel is one way to edit it.
        terms = redact.write_blocklist_file(config.blocklist_path(), payload["blocklist"])
        hub.set_blocklist(terms)

    # Changes that alter the audio path or model behaviour need a session cycle.
    needs_restart = any(
        f in updates
        for f in ("audio_device", "input_channel", "source_language", "realtime")
    )
    if needs_restart and engine.running:
        await engine.restart()

    resp = JSONResponse({"config": config.redacted(), "status": engine.status()})
    if "admin_token" in updates:
        # New token, so every session issued against the old one dies; the
        # operator who made the change keeps a fresh one.
        _SESSIONS.clear()
        _set_session_cookie(resp, request)
    return resp


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
