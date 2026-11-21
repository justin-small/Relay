"""Dante admin routes (#257). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audit
from .. import require
from .monitor import monitor
from .routing import ARC_MODERN_MESSAGE, Refused, needs_modern_format, routing
from .service import service

router = APIRouter()
VIEW = [Depends(require("dante.view"))]
CONTROL = [Depends(require("dante.control"))]
MANAGE = [Depends(require("dante.manage"))]


@router.get("/api/admin/dante", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.post("/api/admin/dante/scan", dependencies=MANAGE)
async def admin_scan(request: Request):
    await service.rescan()
    audit.note(request, "dante.scan", "Looked for Dante devices again")
    return service.snapshot()


@router.get("/api/admin/dante/history", dependencies=VIEW)
def admin_history(limit: int = 100):
    return {"history": monitor.history(limit)}


@router.post("/api/admin/dante/star", dependencies=MANAGE)
async def admin_star(request: Request):
    """Star or unstar a transmit channel for live levels: {"device", "channel", "on"}."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    device, channel, on = body.get("device"), body.get("channel"), body.get("on")
    if not (isinstance(device, str) and isinstance(channel, str) and isinstance(on, bool)
            and 0 < len(device) <= 80 and 0 < len(channel) <= 80):
        raise HTTPException(400, "Send the device, the channel and on true or false.")
    known = {(d["name"], c) for d in service.snapshot()["devices"] for c in d["transmit"]}
    if (device, channel) not in known:
        raise HTTPException(404, "Relay has not seen that channel.")
    monitor.star(device, channel, on)
    audit.note(request, "dante.star", f"{'Starred' if on else 'Unstarred'} {channel} on {device} for live levels")
    return {"starred": sorted([d, c] for d, c in monitor.starred())}


# ------------------------------------------------------------------------------------------------ routing (#259)
async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {}


def _who(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return (getattr(user, "name", "") or getattr(user, "username", "") or "an operator") if user else "an operator"


def _text(v, n: int = 160) -> str | None:
    return v if isinstance(v, str) and len(v) <= n else None


def _channel(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 4096 else None


def _confirmed(body: dict) -> None:
    if body.get("confirm") is not True:
        raise HTTPException(400, "Relay changes routing only after the operator confirms: send confirm true.")


@router.get("/api/admin/dante/routing", dependencies=VIEW)
def routing_state():
    """Every device with its transmit channels and receive channels (what each listens to), the locks, the last change
    Undo would reverse, and the saved presets."""
    devices = [{"name": d["name"], "reachable": d.get("reachable"), "transmit": d["transmit"],
                "receive": d.get("subscriptions"),
                "read_only": ARC_MODERN_MESSAGE if needs_modern_format(d) else ""} for d in service.devices_copy()]
    return {"devices": devices, "locks": sorted([d, c] for d, c in routing.locks()), "undo": routing.undo_info(),
            "presets": routing.presets()}


@router.post("/api/admin/dante/routing/subscribe", dependencies=CONTROL)
async def routing_subscribe(request: Request):
    """Subscribe a receive channel to a source, or clear it: {"device", "channel", "source" ("" clears), "confirm": true}."""
    body = await _body(request)
    device, channel, source = _text(body.get("device"), 80), _channel(body.get("channel")), _text(body.get("source"))
    if not device or channel is None or source is None:
        raise HTTPException(400, "Send the device, the receive channel number and the source (empty to clear it).")
    _confirmed(body)
    who = _who(request)
    try:
        row = await asyncio.to_thread(routing.subscribe, device, channel, source, who)
    except Refused as e:
        raise HTTPException(409, str(e)) from None
    audit.note(request, "dante.subscribe", f"{device} channel {channel}: {source or 'cleared'} ({row['result']})",
               target=device)
    return {"result": row, "undo": routing.undo_info()}


@router.post("/api/admin/dante/routing/undo", dependencies=CONTROL)
async def routing_undo(request: Request):
    body = await _body(request)
    _confirmed(body)
    try:
        results = await asyncio.to_thread(routing.undo, _who(request))
    except Refused as e:
        raise HTTPException(409, str(e)) from None
    audit.note(request, "dante.undo", f"Undid the last Dante routing change ({len(results)} subscriptions)")
    return {"results": results, "undo": routing.undo_info()}


@router.post("/api/admin/dante/routing/lock", dependencies=MANAGE)
async def routing_lock(request: Request):
    """Lock or unlock a receive channel so Relay never changes it: {"device", "channel", "on"}."""
    body = await _body(request)
    device, channel, on = _text(body.get("device"), 80), _channel(body.get("channel")), body.get("on")
    if not device or channel is None or not isinstance(on, bool):
        raise HTTPException(400, "Send the device, the receive channel number and on true or false.")
    if not any(d["name"] == device for d in service.devices_copy()):
        raise HTTPException(404, "Relay has not seen that device.")
    routing.set_lock(device, channel, on)
    audit.note(request, "dante.lock", f"{'Locked' if on else 'Unlocked'} {device} receive channel {channel}", target=device)
    return {"locks": sorted([d, c] for d, c in routing.locks())}


# ------------------------------------------------------------------------------------------------ presets (#260)
@router.post("/api/admin/dante/presets", dependencies=MANAGE)
async def preset_save(request: Request):
    """Save the current routing as a preset: {"name", "devices": [] (empty for all)}. Saving a name again replaces it."""
    body = await _body(request)
    name, scope = _text(body.get("name"), 80), body.get("devices", [])
    if not name or not isinstance(scope, list) or not all(isinstance(x, str) and len(x) <= 80 for x in scope):
        raise HTTPException(400, "Send a name and, optionally, the device names to cover.")
    try:
        saved = routing.save_preset(name, scope, _who(request))
    except Refused as e:
        raise HTTPException(409, str(e)) from None
    audit.note(request, "dante.preset_save", f"Saved Dante routing preset {saved['name']} ({saved['count']} receive channels)")
    return {"saved": saved, "presets": routing.presets()}


@router.delete("/api/admin/dante/presets/{preset_id}", dependencies=MANAGE)
def preset_delete(preset_id: int, request: Request):
    try:
        name = routing.delete_preset(preset_id)
    except Refused as e:
        raise HTTPException(404, str(e)) from None
    audit.note(request, "dante.preset_delete", f"Deleted Dante routing preset {name}")
    return {"presets": routing.presets()}


@router.get("/api/admin/dante/presets/{preset_id}/compare", dependencies=VIEW)
def preset_compare(preset_id: int):
    """The preset against the live network: what would change, what is locked, what is missing."""
    try:
        return routing.compare(preset_id)
    except Refused as e:
        raise HTTPException(404, str(e)) from None


@router.post("/api/admin/dante/presets/{preset_id}/apply", dependencies=CONTROL)
async def preset_apply(preset_id: int, request: Request):
    body = await _body(request)
    _confirmed(body)
    try:
        out = await asyncio.to_thread(routing.apply_preset, preset_id, _who(request))
    except Refused as e:
        raise HTTPException(409, str(e)) from None
    audit.note(request, "dante.preset_apply", f"Applied Dante routing preset {out['preset']['name']} "
                                              f"({len(out['results'])} subscriptions, {out['failed']} failed)")
    return {**out, "undo": routing.undo_info()}
