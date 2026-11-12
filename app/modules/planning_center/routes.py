"""Planning Center's routes: the admin page's connection and LIVE view, the Service feed, and the
panels' Next / Previous / Take control / Release presses. Mounted by the module registry, so every one of
them answers 404 while the module is off."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ... import config, panels
from ...scheduler import scheduler
from ...panels import registry as panel_registry
from ... import modules as shared
from .. import event_stream, read_limited, require_admin, sse
from . import pccontrol, planningcenter as pc
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


@router.post("/api/admin/planning-center", dependencies=[Depends(require_admin)])
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
    await follower.poke()
    return _pc_body()


@router.post("/api/admin/planning-center/test", dependencies=[Depends(require_admin)])
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


@router.post("/api/admin/planning-center/service-types/refresh", dependencies=[Depends(require_admin)])
async def admin_planning_center_service_types_refresh():
    """The account's service types, read from Planning Center. The page calls this after Save
    and once a minute while the list is on screen; it changes nothing."""
    return await admin_planning_center_test()


@router.post("/api/admin/planning-center/service-types", dependencies=[Depends(require_admin)])
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


@router.post("/api/admin/planning-center/notes", dependencies=[Depends(require_admin)])
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


@router.get("/api/admin/planning-center/live", dependencies=[Depends(require_admin)])
async def admin_planning_center_live():
    """Today's plan and its LIVE state for each followed service type, and
    the presses made from panels."""
    return {**follower.snapshot(), "actions": control.recent()}


@router.post("/api/admin/planning-center/plan", dependencies=[Depends(require_admin)])
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


@router.delete("/api/admin/planning-center", dependencies=[Depends(require_admin)])
async def admin_planning_center_disconnect():
    config.save({"planning_center": {}}, replace=("planning_center",))
    log.info("Planning Center disconnected")
    await follower.poke()
    return _pc_body()


# --------------------------------------------------------------- ProPresenter (#138)
