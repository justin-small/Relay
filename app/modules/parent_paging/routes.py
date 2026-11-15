"""Parent paging routes (#220). Mounted by the module registry, so each answers 404 while the module is off.

The room pages (/paging/<secret>) need no sign-in: the secret in the link is the room's key. They list children's
names, so they answer only on the operator (HTTPS) port and never through the remote access tunnel, and a link an
admin replaces stops working at once.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ... import audit, config, serving
from .. import require
from . import checkins, paging
from .paging import pager

router = APIRouter()
VIEW = [Depends(require("parent_paging.view"))]
CONTROL = [Depends(require("parent_paging.control"))]
MANAGE = [Depends(require("parent_paging.manage"))]
ADMIN = "/api/admin/parent-paging"


def _refuse(exc: Exception) -> JSONResponse:
    if isinstance(exc, (paging.Refused, checkins.Unavailable)):
        return JSONResponse({"error": exc.message}, status_code=getattr(exc, "status", 502))
    raise exc


def _pc_client():
    from ..planning_center import planningcenter as pc

    client = pc.client_from(config.get())
    if client is None:
        raise paging.Refused("Planning Center is not set up. Save its application ID and secret first.", 409)
    return client


def via_tunnel(request: Request) -> bool:
    """A request that came in through the remote access tunnel (Cloudflare marks every one it forwards)."""
    return any(h in request.headers for h in ("cf-ray", "cf-connecting-ip", "cf-visitor"))


def _on_viewer_port(request: Request) -> bool:
    server = request.scope.get("server") or ()
    port = server[1] if len(server) > 1 else None
    return serving.published() and port == serving.viewer_port(config.get()["port"])


def _room_or_404(secret: str, request: Request) -> dict:
    if via_tunnel(request) or _on_viewer_port(request):
        raise HTTPException(404, "Not Found")
    room = paging.room_by_secret(secret)
    if room is None:
        raise HTTPException(404, "Not Found")
    return room


# ================================================================== the room's page (no sign-in, the link's secret)

@router.get("/paging/{secret}")
def room_page(secret: str, request: Request):
    from .. import templates

    room = _room_or_404(secret, request)
    return templates.TemplateResponse(request, "parent_paging/room.html", {"room": paging.public_room(room)},
                                      headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                               "X-Robots-Tag": "noindex"})


def _room_state(room: dict, kids: list[dict], note: str = "") -> dict:
    mode = paging.settings()["mode"]
    up = {p.kid: p.id for p in pager.for_room(room["id"])}
    return {
        "room": room["name"],
        "mode": mode,
        "kids": [{**k, "page": up.get(k["id"], "")} for k in kids],
        "pages": [pager.view(p, mode=mode) for p in pager.for_room(room["id"])],
        "note": note,
    }


@router.get("/paging/{secret}/state")
async def room_state(secret: str, request: Request):
    room = _room_or_404(secret, request)
    note = ""
    try:
        kids = await asyncio.to_thread(checkins.cache.kids, _pc_client(), room["location_id"])
    except (paging.Refused, checkins.Unavailable) as exc:
        kids, note = checkins.cache.known(room["location_id"]), exc.message
    return JSONResponse(_room_state(room, kids, note), headers={"Cache-Control": "no-store"})


@router.post("/paging/{secret}/page")
async def room_send(secret: str, request: Request, payload: dict):
    room = _room_or_404(secret, request)
    kid_id = str(payload.get("kid") or "")
    # The code comes from Relay's own list for the room, never from the page: a link can only page a child who is
    # checked in to its room.
    kid = next((k for k in checkins.cache.known(room["location_id"]) if k["id"] == kid_id), None)
    if kid is None:
        return JSONResponse({"error": "That child is not on this room's list any more. Refresh and try again."},
                            status_code=404)
    try:
        page = pager.page(room, kid)
    except paging.Refused as exc:
        return _refuse(exc)
    pager.poke()
    return {"ok": True, "page": pager.view(page)}


@router.post("/paging/{secret}/arrived/{pid}")
async def room_arrived(secret: str, pid: str, request: Request):
    room = _room_or_404(secret, request)
    try:
        pager.end(pid, room_id=room["id"], why="parent arrived")
    except paging.Refused as exc:
        return _refuse(exc)
    pager.poke()
    return {"ok": True}


# ================================================================== admin

def _status() -> dict:
    from .. import modules

    s = paging.settings()
    return {
        "settings": paging.public(s),
        "pages": [pager.view(p, mode=s["mode"]) for p in pager.pages],
        "on_screen": pager.codes(s["mode"]),
        "error": pager.error,
        "rev": pager.rev,
        "needs": [name for mid, name in (("planning_center", "Planning Center"), ("propresenter", "ProPresenter"))
                  if not modules.enabled(mid)],
    }


@router.get(ADMIN, dependencies=VIEW)
def admin_state():
    return _status()


@router.put(ADMIN + "/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    s = paging.settings()
    merged = paging.clean_stored({**s, **{k: payload[k] for k in ("mode", "message", "token", "timeout_min")
                                          if k in payload}})
    old_mode = s["mode"]
    config.save({paging.KEY: merged}, replace=(paging.KEY,))
    audit.note(request, "parent_paging.settings", "Changed the parent paging setup")
    if merged["mode"] != old_mode or merged["message"] != s["message"] or merged["token"] != s["token"]:
        pager.force = pager.sent is not None   # a different message or way: write it again
    pager.poke()
    return _status()


@router.get(ADMIN + "/locations", dependencies=MANAGE)
async def admin_locations():
    try:
        rows = await asyncio.to_thread(checkins.locations, _pc_client())
    except (paging.Refused, checkins.Unavailable) as exc:
        return _refuse(exc)
    return {"locations": rows}


@router.post(ADMIN + "/rooms", dependencies=MANAGE)
def admin_add_room(request: Request, payload: dict):
    s = paging.settings()
    if len(s["rooms"]) >= paging.MAX_ROOMS:
        return JSONResponse({"error": f"At most {paging.MAX_ROOMS} rooms can be set up."}, status_code=400)
    loc = str(payload.get("location_id") or "")
    if not paging._LOCATION.match(loc):
        return JSONResponse({"error": "Choose a Check-Ins room."}, status_code=400)
    room = paging.new_room(str(payload.get("name") or ""), loc, str(payload.get("event") or ""))
    config.save({paging.KEY: {**s, "rooms": s["rooms"] + [room]}}, replace=(paging.KEY,))
    audit.note(request, "parent_paging.room_add", "Added a parent paging room", target=room["name"])
    return {**_status(), "room": paging.public_room(room)}


def _save_room(rid: str, change) -> dict:
    s = paging.settings()
    room = paging.room_by_id(rid)
    if room is None:
        raise HTTPException(404, "Not Found")
    new = change(room)
    rooms = [r for r in (new if r["id"] == rid else r for r in s["rooms"]) if r is not None]
    config.save({paging.KEY: {**s, "rooms": rooms}}, replace=(paging.KEY,))
    return room


@router.get(ADMIN + "/rooms/{rid}/link", dependencies=MANAGE)
def admin_room_link(rid: str, request: Request):
    room = paging.room_by_id(rid)
    if room is None:
        raise HTTPException(404, "Not Found")
    # Built from the address the admin is using now: the operator (HTTPS) port, which is where the page answers.
    return {"path": f"/paging/{room['secret']}", "url": str(request.base_url).rstrip("/") + f"/paging/{room['secret']}"}


@router.post(ADMIN + "/rooms/{rid}/new-link", dependencies=MANAGE)
def admin_room_new_link(rid: str, request: Request):
    room = _save_room(rid, lambda r: {**r, "secret": paging.new_room(r["name"], r["location_id"])["secret"]})
    audit.note(request, "parent_paging.room_link", "Replaced a parent paging room's link", target=room["name"])
    return admin_room_link(rid, request)


@router.put(ADMIN + "/rooms/{rid}", dependencies=MANAGE)
def admin_rename_room(rid: str, request: Request, payload: dict):
    name = paging._text(payload.get("name"), 80)
    if not name:
        return JSONResponse({"error": "Give the room a name."}, status_code=400)
    room = _save_room(rid, lambda r: {**r, "name": name})
    audit.note(request, "parent_paging.room_rename", "Renamed a parent paging room", target=room["name"])
    return _status()


@router.delete(ADMIN + "/rooms/{rid}", dependencies=MANAGE)
def admin_delete_room(rid: str, request: Request):
    room = _save_room(rid, lambda r: None)
    pager.end_room(rid)
    pager.poke()
    audit.note(request, "parent_paging.room_delete", "Removed a parent paging room", target=room["name"])
    return _status()


@router.post(ADMIN + "/pages/{pid}/show", dependencies=CONTROL)
def admin_show(pid: str, request: Request):
    try:
        page = pager.show(pid)
    except paging.Refused as exc:
        return _refuse(exc)
    audit.note(request, "parent_paging.show", "Showed a parent page", target=page.code)
    pager.poke()
    return _status()


@router.post(ADMIN + "/pages/{pid}/clear", dependencies=CONTROL)
def admin_clear(pid: str, request: Request):
    try:
        pager.end(pid, why="cleared by the operator")
    except paging.Refused as exc:
        return _refuse(exc)
    audit.note(request, "parent_paging.clear", "Cleared a parent page")
    pager.poke()
    return _status()


@router.post(ADMIN + "/pages/clear", dependencies=CONTROL)
def admin_clear_all(request: Request):
    n = pager.end_all()
    audit.note(request, "parent_paging.clear_all", "Cleared every parent page", detail={"pages": n})
    pager.poke()
    return _status()
