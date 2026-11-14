"""Planning Center's routes: the admin page's connection and LIVE view, the Service feed, and the
panels' Next / Previous / Take control / Release presses. Mounted by the module registry, so every one of
them answers 404 while the module is off."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ... import config, panels
from ...scheduler import scheduler
from ...panels import registry as panel_registry
from ... import modules as shared
from .. import event_stream, read_limited, require, sse
from . import pccontrol, planningcenter as pc, rundown
from .pclive import follower
from .servicefeed import feed

log = logging.getLogger("relay.planning_center")

router = APIRouter()
# Presses from panels, and from the ProPresenter link, go through this one path (pccontrol.py).
control = pccontrol.Control(follower, panel_registry, feed)


def _service_stream(st: str):
    """Today's plan and LIVE state for one followed service type (servicefeed.py)."""
    queue = feed.subscribe(st)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=shared.HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield sse(feed.tick_payload(st)).encode()
                    continue
                yield frame
        finally:
            feed.unsubscribe(st, queue)

    return event_stream(gen())


@router.post("/api/panels/{panel_id}/service/{action}")
async def panel_service_action(panel_id: str, action: str, request: Request):
    """Next / Previous / Take control / Release from a panel the operator lets
    control, with its key (pccontrol.py). Answers with the new Service feed
    state, or {"ok": false, "error": <code>} for the panel to word."""
    if action not in pccontrol.ACTIONS:
        raise HTTPException(404, "unknown action")
    if not panels.valid_id(panel_id) or not panel_registry.known(panel_id):
        raise HTTPException(404, "unknown panel")
    raw = await read_limited(request, pccontrol.BODY_MAX_BYTES)
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    try:
        state = await control.press(panel_id, action, body)
    except pccontrol.Refused as exc:
        return JSONResponse({"ok": False, "error": exc.code}, status_code=exc.status)
    return {"ok": True, "service": state}


def _pc_body() -> dict:
    return {"planning_center": config.redacted()["planning_center"]}


@router.post("/api/admin/planning-center", dependencies=[Depends(require("planning_center.manage"))])
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
        **saved,
        "app_id": app_id,
        "secret": secret,
        "service_types": [] if changed else saved["service_types"],
        "plan_overrides": {} if changed else saved["plan_overrides"],
    }}, replace=("planning_center",))
    log.info("Planning Center token %s", "saved" if changed else "unchanged")
    await follower.poke()
    return _pc_body()


@router.post("/api/admin/planning-center/test", dependencies=[Depends(require("planning_center.manage"))])
async def admin_planning_center_test():
    """Try the saved token and list the account's service types."""
    client = pc.client_from(config.get())
    if client is None:
        return JSONResponse({"error": "Save an application ID and secret first."}, status_code=400)
    try:
        types = await asyncio.get_running_loop().run_in_executor(None, client.service_types)
    except pc.PlanningCenterError as exc:
        status = {"auth": 400, "rate": 429, "network": 502}.get(exc.kind, 502)
        log.warning("Planning Center test: %s", exc)
        return JSONResponse({"error": pc.operator_message(exc)}, status_code=status)
    return {**_pc_body(), "service_types": types}


@router.post("/api/admin/planning-center/service-types/refresh", dependencies=[Depends(require("planning_center.manage"))])
async def admin_planning_center_service_types_refresh():
    """The account's service types, read from Planning Center. The page calls this after Save
    and once a minute while the list is on screen; it changes nothing."""
    return await admin_planning_center_test()


@router.post("/api/admin/planning-center/service-types", dependencies=[Depends(require("planning_center.manage"))])
async def admin_planning_center_service_types(payload: dict):
    """Which service types Relay follows."""
    if not pc.connected(config.get()):
        return JSONResponse({"error": "Connect to Planning Center first."}, status_code=400)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return JSONResponse({"error": "ids must be a list."}, status_code=400)
    saved = config.get()["planning_center"]
    config.save({"planning_center": {**saved, "service_types": ids}}, replace=("planning_center",))
    await follower.poke()
    return _pc_body()


@router.post("/api/admin/planning-center/notes", dependencies=[Depends(require("planning_center.manage"))])
async def admin_planning_center_notes(payload: dict):
    """"Show item notes on panels". Off by default: notes can hold names and
    pastoral details, and the service feed is on the viewer port with no login."""
    show = payload.get("show")
    if not isinstance(show, bool):
        return JSONResponse({"error": "show must be true or false."}, status_code=400)
    saved = config.get()["planning_center"]
    config.save({"planning_center": {**saved, "show_notes": show}}, replace=("planning_center",))
    log.info("Planning Center item notes on panels: %s", "shown" if show else "hidden")
    feed.refresh()
    return _pc_body()


@router.get("/api/admin/planning-center/live", dependencies=[Depends(require("planning_center.view"))])
async def admin_planning_center_live():
    """Today's plan and its LIVE state for each followed service type, and
    the presses made from panels."""
    return {**follower.snapshot(), "actions": control.recent()}


@router.post("/api/admin/planning-center/plan", dependencies=[Depends(require("planning_center.control"))])
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
        track = follower.tracks.get(st)
        day = track.day if track and track.day else pc.local_day(
            scheduler.now(), follower.zone or follower.fallback_zone())
        overrides[st] = {"plan": plan, "day": day.isoformat()}
    config.save({"planning_center": {**saved, "plan_overrides": overrides}}, replace=("planning_center",))
    log.info("Planning Center, service type %s: operator %s", st,
             f"picked plan {plan} for today" if plan else "cleared the plan pick")
    await follower.poke()
    return follower.snapshot()


@router.delete("/api/admin/planning-center", dependencies=[Depends(require("planning_center.manage"))])
async def admin_planning_center_disconnect():
    config.save({"planning_center": {}}, replace=("planning_center",))
    log.info("Planning Center disconnected")
    await follower.poke()
    return _pc_body()


# --------------------------------------------------------------- ProPresenter (#138)


# --------------------------------------------------------------- the rundown (#148)
RUNDOWN_TICK_S = 1.0
RUNDOWN_RESEND_S = 5.0


def _rundown_track(st: str):
    """The followed service type the rundown is for: the one asked for, else one LIVE is running, else the first
    with a plan today."""
    tracks = follower.tracks
    if st:
        return tracks.get(st)
    with_plan = [t for t in tracks.values() if t.plan is not None]
    now = rundown.utcnow()
    return next((t for t in with_plan if pc.running(t.live, now)), with_plan[0] if with_plan else None)


def rundown_now(st: str = "", want_time: str = "") -> dict:
    """The rundown as it stands; a changed mic draw is saved, so it holds across restarts."""
    tr = _rundown_track(st)
    settings = config.get()["planning_center"]
    if tr is None:
        return {"type": "rundown", "service_type": st or None, "now": rundown.utcnow(), "plan": None, "items": [],
                "times": [], "time": None, "header": None, "people": [], "mics": [], "notes": None,
                "live": False, "controller": None}
    msg, draws = rundown.build(tr, settings, rundown.utcnow(), follower.zone or follower.fallback_zone(), want_time)
    if draws != settings["rundown"]["draws"]:
        config.save({"planning_center": {**settings, "rundown": {**settings["rundown"], "draws": draws}}},
                     replace=("planning_center",))
    return msg


def via_tunnel(request: Request) -> bool:
    """A request that came in through the remote access tunnel (Cloudflare marks every one it forwards)."""
    return any(h in request.headers for h in ("cf-ray", "cf-connecting-ip", "cf-visitor"))


def _rundown_stream(st: str, want_time: str):
    async def gen():
        last, sent_at = "", 0.0
        while True:
            msg = rundown_now(st, want_time)
            sig = rundown.signature(msg)
            if sig != last or msg["now"] - sent_at >= RUNDOWN_RESEND_S:
                last, sent_at = sig, msg["now"]
                yield sse(msg).encode()
            await asyncio.sleep(RUNDOWN_TICK_S)

    return event_stream(gen())


def _who(user) -> str:
    for attr in ("display_name", "name", "username", "email"):
        v = getattr(user, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    return "Admin page"


def _rundown_settings_view() -> dict:
    rs = config.get()["planning_center"]["rundown"]
    return {k: v for k, v in rs.items() if k != "draws"}


@router.get("/api/admin/planning-center/rundown", dependencies=[Depends(require("planning_center.view"))])
async def admin_planning_center_rundown(type: str = "", time: str = ""):
    """The rundown as /screen shows it, the settings behind it, and every team and position on today's plans
    (for the mic map)."""
    st = type if type in follower.tracks else ""
    want = time if re.fullmatch(r"\d{4}", time or "") else ""
    return {"rundown": rundown_now(st, want), "settings": _rundown_settings_view(),
            "positions": rundown.positions(list(follower.tracks.values())),
            "service_types": [{"id": s, "name": follower.names.get(s) or f"Service type {s}"}
                              for s in follower.tracks],
            "actions": control.recent()}


@router.post("/api/admin/planning-center/rundown/settings", dependencies=[Depends(require("planning_center.manage"))])
async def admin_planning_center_rundown_settings(payload: dict):
    """The rundown on /screen (on or off), the note fields it reads, and the mics by position."""
    saved = config.get()["planning_center"]
    rs = dict(saved["rundown"])
    for k in ("leader_note", "leader_position", "sermon_note"):
        if k in payload:
            if not isinstance(payload[k], str):
                return JSONResponse({"error": f"{k} must be text."}, status_code=400)
            rs[k] = payload[k]
    if "screen" in payload:
        if not isinstance(payload["screen"], bool):
            return JSONResponse({"error": "screen must be true or false."}, status_code=400)
        rs["screen"] = payload["screen"]
    if "mics" in payload:
        if not isinstance(payload["mics"], list):
            return JSONResponse({"error": "mics must be a list."}, status_code=400)
        rs["mics"] = payload["mics"]
    config.save({"planning_center": {**saved, "rundown": rs}}, replace=("planning_center",))
    log.info("Planning Center rundown settings saved (screen %s)", "on" if rs["screen"] else "off")
    return {"settings": _rundown_settings_view()}


@router.post("/api/admin/planning-center/rundown/redraw", dependencies=[Depends(require("planning_center.control"))])
async def admin_planning_center_rundown_redraw(payload: dict):
    """Draw the mics again for one position (or every position) on today's plan and service time."""
    st, want = str(payload.get("type") or ""), str(payload.get("time") or "")
    msg = rundown_now(st if st in follower.tracks else "", want if re.fullmatch(r"\d{4}", want) else "")
    if not msg["plan"] or not msg["time"]:
        return JSONResponse({"error": "There is no plan and service time to draw for."}, status_code=409)
    saved = config.get()["planning_center"]
    team, position = str(payload.get("team") or ""), str(payload.get("position") or "")
    prefix = f"{msg['plan']['id']}:{msg['time']['id']}:"
    draws = {k: v for k, v in saved["rundown"]["draws"].items()
             if not (k.startswith(prefix) and (not position or k == prefix + f"{team}|{position}"))}
    config.save({"planning_center": {**saved, "rundown": {**saved["rundown"], "draws": draws}}},
                replace=("planning_center",))
    log.info("Planning Center mics redrawn for %s", position or "every position")
    return {"rundown": rundown_now(st if st in follower.tracks else "", want if re.fullmatch(r"\d{4}", want) else "")}


@router.post("/api/admin/planning-center/live/{action}")
async def admin_planning_center_live_action(action: str, payload: dict,
                                            user=Depends(require("planning_center.control"))):
    """Next / Previous / Take control / Release from the admin page, with the same rules as a panel's press:
    Relay presses as the token's owner, and every press is listed with the panels' presses."""
    if action not in pccontrol.ACTIONS:
        raise HTTPException(404, "unknown action")
    st = str(payload.get("type") or "")
    try:
        await control.press_admin(st, action, _who(user))
    except pccontrol.Refused as exc:
        return JSONResponse({"ok": False, "error": exc.code, "message": pccontrol.ADMIN_WORDS.get(exc.code, "")},
                            status_code=pccontrol.REFUSALS[exc.code])
    return {"ok": True, "rundown": rundown_now(st)}
