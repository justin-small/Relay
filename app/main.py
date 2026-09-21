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
import urllib.parse

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, languages, redact
from .engine import TRANSCRIPTION_STREAM, TRANSLATION_STREAM, engine
from .hub import hub

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
    engine.bind(asyncio.get_running_loop())
    app.state.blocklist_task = asyncio.create_task(_watch_blocklist())
    if not (cfg.get("admin_token") or "").strip():
        # _token_ok() returns False on an empty stored token, so the panel is
        # locked rather than open -- but an operator who cannot log in deserves
        # to know why. Run setup.command / setup.bat to set one.
        log.warning(
            "Admin token is not set: the operator panel will reject every login. "
            "Run setup.command (macOS) or setup.bat (Windows) to set one."
        )
    log.info("Viewer pages: %s", " ".join(_urls(cfg["port"])))
    if int(cfg["admin_port"]) != int(cfg["port"]):
        log.info("Operator panel: %s", " ".join(u + "admin" for u in _urls(cfg["admin_port"])))
    if os.environ.get("RELAY_DEMO") == "1":
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


def _cookie_token(request: Request) -> str:
    """The admin token carried by the cookie, un-escaped.

    Cookies are percent-encoded on the way out (see admin_login) because a
    header cannot carry arbitrary Unicode; ASCII tokens are unaffected either
    way, so cookies issued before that change still read back correctly.
    """
    raw = request.cookies.get(ADMIN_COOKIE) or ""
    try:
        return urllib.parse.unquote(raw)
    except Exception:
        return raw


def require_admin(request: Request) -> bool:
    token = (config.get().get("admin_token") or "").strip()
    supplied = _cookie_token(request) or request.headers.get("x-admin-token") or ""
    if not _token_ok(supplied, token):
        raise HTTPException(status_code=401, detail="Admin token required")
    return True


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
    return templates.TemplateResponse("index.html", ctx)


@app.get("/transcription", response_class=HTMLResponse)
async def page_transcription(request: Request):
    ctx = _viewer_ctx(request) | {"mode": "transcription"}
    return templates.TemplateResponse("viewer.html", ctx)


@app.get("/translation", response_class=HTMLResponse)
async def page_translation(request: Request):
    ctx = _viewer_ctx(request) | {"mode": "translation"}
    return templates.TemplateResponse("viewer.html", ctx)


@app.get("/both", response_class=HTMLResponse)
async def page_both(request: Request):
    ctx = _viewer_ctx(request) | {"mode": "both"}
    return templates.TemplateResponse("viewer.html", ctx)


@app.get("/present", response_class=HTMLResponse)
async def page_present(request: Request):
    """Projector / confidence-monitor view: chrome hidden, bottom-anchored, big
    type. Defaults to the translation feed; ?mode= picks another. Present is also
    reachable as ?present=1 on any viewer route (see _viewer_ctx)."""
    mode = request.query_params.get("mode", "translation")
    if mode not in ("transcription", "translation", "both"):
        mode = "translation"
    ctx = _viewer_ctx(request) | {"mode": mode, "present": True}
    return templates.TemplateResponse("viewer.html", ctx)


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
    return templates.TemplateResponse("viewer.html", ctx)


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
    return templates.TemplateResponse("viewer.html", ctx)


@app.get("/api/targets")
async def api_targets():
    """First-paint + resync endpoint for the viewer picker. The live set is now
    pushed over the caption SSE (see engine.targets_payload); this stays for the
    initial load and the visibilitychange resync. Live targets only -- the picker
    never offers a dead language."""
    data = engine.targets_payload()
    data.pop("type", None)
    if not data["live"] and os.environ.get("RELAY_DEMO") == "1":
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
    token = (config.get().get("admin_token") or "").strip()
    supplied = _cookie_token(request)
    if not _token_ok(supplied, token):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": None}, status_code=200
        )
    return templates.TemplateResponse("admin.html", {"request": request})


@app.post("/admin/login")
async def admin_login(request: Request, token: str = Form(...)):
    real = (config.get().get("admin_token") or "").strip()
    if not _token_ok(token.strip(), real):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Incorrect token."}, status_code=401
        )
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        ADMIN_COOKIE,
        urllib.parse.quote(real, safe=""),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@app.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(ADMIN_COOKIE)
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
async def admin_config(payload: dict):
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

    cfg = config.save(updates)
    hub.history_lines = cfg["history_lines"]

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
        resp.set_cookie(
            ADMIN_COOKIE,
            updates["admin_token"],
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )
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
    try:
        return await engine.start()
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc), **engine.status()}, status_code=400)


@app.post("/api/admin/stop", dependencies=[Depends(require_admin)])
async def admin_stop():
    return await engine.stop()


@app.get("/healthz")
async def healthz():
    return {"ok": True, "running": engine.running}
