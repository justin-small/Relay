"""The crew page's routes. Mounted by the module registry, so each answers 404 while the module is off, and the
registry's panel-only rule (Module.panel_prefixes) answers 404 for every /crew path on the viewer port.

Public on purpose, because a browser fetches them without the sign-in cookie: the manifest, the service worker and
its offline page. They carry nothing but the page's name and icons."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ... import audit, config
from .. import modules as registry, require
from . import calltimes, chat, paging, people

router = APIRouter()
VIEW = [Depends(require("crew.view"))]
MANAGE = [Depends(require("crew.manage"))]
PAGE = [Depends(require("crew.page"))]
USE = [Depends(require("crew.use"))]
CHAT = [Depends(require("crew.chat"))]

KEY = "crew"
HERE = Path(__file__).resolve().parent
NO_STORE = {"Cache-Control": "no-store"}
ICONS = [
    {"src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
]


def _settings() -> dict:
    return config.get()[KEY]


def _pkg():
    from .. import page_gate, templates

    return page_gate, templates


@router.get("/crew", response_class=HTMLResponse)
async def crew_page(request: Request):
    """The crew page: sign-in for a signed-out visitor, "not allowed" for a role without crew.use."""
    gate, templates = _pkg()
    got = await asyncio.to_thread(gate, request, "crew.use")
    if isinstance(got, Response):
        return got
    s = _settings()
    try:
        role = await asyncio.to_thread(people.profile, got.id)
        role = role["role"]
    except Exception:
        role = ""      # the role is a label: the page works without it
    return templates.TemplateResponse(request, "crew/crew.html", {
        "app_name": s["name"], "show_plan": s["show_plan"], "user": got, "crew_role": role,
        "tools": registry.user_nav(got), "tabs": registry.crew_nav(got),
        "module_scripts": registry.crew_scripts(got),
    }, headers=NO_STORE)


@router.get("/crew/_/manifest.webmanifest")
def crew_manifest():
    name = _settings()["name"]
    return JSONResponse({
        "name": name, "short_name": name, "id": "/crew", "start_url": "/crew", "scope": "/crew",
        "display": "standalone", "background_color": "#000000", "theme_color": "#000000", "icons": ICONS,
    }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/crew/_/sw.js")
def crew_service_worker():
    """The service worker. Its default scope would be /crew/_/, so Service-Worker-Allowed widens it to the page."""
    return Response((HERE / "static" / "sw.js").read_bytes(), media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/crew"})


@router.get("/crew/_/offline", response_class=HTMLResponse)
def crew_offline():
    return HTMLResponse((HERE / "templates" / "offline.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-cache"})


@router.get("/crew/api/plan", dependencies=USE)
def crew_plan():
    """Today's plan for the page, from Planning Center when that module is on and the setting allows it. Titles
    and times only: the people, mics and notes stay on the production team's screen."""
    if not _settings()["show_plan"] or not registry.enabled("planning_center"):
        return {"available": False, "plan": None}
    from ..planning_center.routes import rundown_now

    msg = rundown_now("", "")
    if not msg.get("plan"):
        return {"available": True, "plan": None}
    return {"available": True, "plan": {
        "title": msg["plan"]["title"], "dates": msg["plan"]["dates"],
        "time": (msg.get("time") or {}).get("hhmm"), "zone": msg.get("zone"),
        "current": (msg.get("header") or {}).get("current"),
        "items": [{"title": i["title"], "kind": i["kind"], "planned": i["planned"], "state": i["state"]}
                  for i in msg["items"]],
    }}


@router.get("/api/admin/crew", dependencies=VIEW)
def admin_get():
    return _settings()


@router.post("/api/admin/crew", dependencies=MANAGE)
def admin_save(request: Request, payload: dict):
    from . import MODULE

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, "Give the crew page a name.")
    show = payload.get("show_plan")
    if not isinstance(show, bool):
        raise HTTPException(400, "Choose whether the page shows the plan.")
    clean = MODULE.clean({"name": name, "show_plan": show})
    config.save({KEY: clean}, replace=(KEY,))
    audit.note(request, "crew.change", "Changed the crew page's setup", detail={"show_plan": clean["show_plan"]})
    return _settings()


# -- roles and Planning Center names (#227) --------------------------------------------------------------------
def _people_call(fn, *args):
    from ... import db

    try:
        return fn(*args)
    except people.Invalid as exc:
        raise HTTPException(exc.status, exc.message)
    except db.Unavailable:
        raise HTTPException(503, "The user database is not available.")


@router.get("/api/admin/crew/people", dependencies=VIEW)
def people_get():
    return {"people": _people_call(people.people_list),
            "planning_center": registry.enabled("planning_center")}


@router.post("/api/admin/crew/people/scan", dependencies=MANAGE)
def people_scan(request: Request):
    """Match names now, instead of waiting for Planning Center to change."""
    if not registry.enabled("planning_center"):
        raise HTTPException(400, "Turn Planning Center on to match names.")
    from . import MODULE

    MODULE._seen = ""
    _people_call(MODULE._scan)
    return people_get()


@router.put("/api/admin/crew/people/{user_id}/role", dependencies=MANAGE)
def people_role(request: Request, user_id: int, payload: dict):
    role = _people_call(people.set_role, user_id, payload.get("role"))
    chat.hub.signed_out(user_id)         # their role channel changed: their open chat reconnects and reloads
    audit.note(request, "crew.role", "Set a crew role", target=user_id, detail={"role": role})
    return {"role": role}


@router.post("/api/admin/crew/people/{user_id}/name", dependencies=MANAGE)
def people_name(request: Request, user_id: int, payload: dict):
    """Accept or dismiss the Planning Center spelling suggested for a person's name."""
    action = payload.get("action")
    if action == "accept":
        name = _people_call(people.accept_name, user_id, request.state.user)
        audit.note(request, "crew.name", "Accepted the Planning Center spelling of a name", target=user_id)
        return {"name": name}
    if action == "dismiss":
        _people_call(people.dismiss_name, user_id)
        return {"ok": True}
    raise HTTPException(400, "Choose accept or dismiss.")


@router.delete("/api/admin/crew/people/{user_id}/match", dependencies=MANAGE)
def people_unlink(request: Request, user_id: int):
    _people_call(people.unlink, user_id)
    audit.note(request, "crew.unmatch", "Removed a Planning Center match", target=user_id)
    return {"ok": True}


# -- pages that buzz until read (#228) --------------------------------------------------------------------------
def _tracks():
    """Today's followed plans, when Planning Center is on (an empty list otherwise)."""
    if not registry.enabled("planning_center"):
        return []
    from ..planning_center.pclive import follower

    return list(follower.tracks.values())


@router.get("/crew/api/push/key", dependencies=USE)
def push_key():
    return {"key": _people_call(paging.public_key)}


@router.get("/crew/api/push", dependencies=USE)
def push_state(request: Request):
    return {"enabled": _people_call(paging.subscribed, request.state.user.id)}


@router.post("/crew/api/push", dependencies=USE)
def push_subscribe(request: Request, payload: dict):
    _people_call(paging.subscribe, request.state.user.id, payload)
    return {"enabled": True}


@router.delete("/crew/api/push", dependencies=USE)
def push_unsubscribe(request: Request, payload: dict):
    _people_call(paging.unsubscribe, request.state.user.id, payload.get("endpoint"))
    return {"enabled": _people_call(paging.subscribed, request.state.user.id)}


def _confirm_page(request: Request, row: dict | None, status: int = 200):
    _, templates = _pkg()
    return templates.TemplateResponse(request, "crew/page.html", {
        "app_name": _settings()["name"], "found": row is not None, "message": (row or {}).get("message", ""),
        "confirmed": bool(row and row["confirmed_at"])}, status_code=status if row else 404, headers=NO_STORE)


@router.get("/crew/page/{token}", response_class=HTMLResponse)
def page_open(request: Request, token: str):
    """A person's own confirm link, opened from the push. The token is the proof: no sign-in is needed, because
    it can do nothing but confirm that one page."""
    return _confirm_page(request, _people_call(paging.by_token, token))


@router.post("/crew/page/{token}", response_class=HTMLResponse)
def page_confirm(request: Request, token: str):
    return _confirm_page(request, _people_call(paging.confirm, token))


@router.get("/api/admin/crew/pages", dependencies=PAGE)
def pages_get():
    return {"pages": _people_call(paging.recent), "targets": _people_call(paging.targets, _tracks()),
            "planning_center": registry.enabled("planning_center")}


@router.post("/api/admin/crew/pages", dependencies=PAGE)
def pages_send(request: Request, payload: dict):
    ids, unmatched = _people_call(paging.resolve, payload.get("users"), payload.get("teams"), _tracks())
    page = _people_call(paging.create, payload.get("message"), request.state.user.id, ids)
    audit.note(request, "crew.page", "Paged the crew", target=page, detail={"people": len(ids)})
    return {"id": page, "people": len(ids), "unmatched": unmatched}


# -- scheduled call-time pages (#229) -----------------------------------------------------------------------------
@router.get("/api/admin/crew/calltimes", dependencies=VIEW)
def calltimes_get():
    return {"rules": _people_call(calltimes.list_rules),
            "teams": sorted(paging.serving(_tracks())), "planning_center": registry.enabled("planning_center")}


@router.post("/api/admin/crew/calltimes", dependencies=MANAGE)
def calltimes_add(request: Request, payload: dict):
    rule = _people_call(calltimes.clean, payload)
    rid = _people_call(calltimes.save, rule)
    audit.note(request, "crew.calltime_add", "Added a call-time page", target=rid, detail={"team": rule["team"]})
    return calltimes_get()


@router.put("/api/admin/crew/calltimes/{rule_id}", dependencies=MANAGE)
def calltimes_change(request: Request, rule_id: int, payload: dict):
    rule = _people_call(calltimes.clean, payload)
    _people_call(calltimes.save, rule, rule_id)
    audit.note(request, "crew.calltime_change", "Changed a call-time page", target=rule_id,
               detail={"team": rule["team"], "enabled": rule["enabled"]})
    return calltimes_get()


@router.delete("/api/admin/crew/calltimes/{rule_id}", dependencies=MANAGE)
def calltimes_delete(request: Request, rule_id: int):
    _people_call(calltimes.delete, rule_id)
    audit.note(request, "crew.calltime_delete", "Removed a call-time page", target=rule_id)
    return calltimes_get()


# -- team chat (#230) ---------------------------------------------------------------------------------------------
@router.get("/crew/api/chat/channels", dependencies=CHAT)
def chat_channels(request: Request):
    return {"me": request.state.user.id, "channels": _people_call(chat.channels_for, request.state.user),
            "moderator": request.state.user.can("crew.moderate")}


@router.get("/crew/api/chat/history", dependencies=CHAT)
def chat_history(request: Request, channel: str, after: int = 0):
    user = request.state.user
    if not _people_call(chat.can_read, user, channel):
        raise HTTPException(403, "You cannot read that channel.")
    return {"channel": channel, "messages": _people_call(chat.history, channel, chat.MIRROR, max(0, after))}


@router.post("/crew/api/chat/send", dependencies=CHAT)
async def chat_send(request: Request, payload: dict):
    user = request.state.user
    msg = await asyncio.to_thread(_people_call, chat.post, user, payload.get("channel"), payload.get("body"))
    chat.hub.publish(msg["channel"], {"type": "message", "message": msg})
    watching = chat.hub.watching()
    asyncio.create_task(asyncio.to_thread(_quiet_notify, msg, watching))
    return {"message": msg}


def _quiet_notify(msg: dict, watching) -> None:
    try:
        chat.notify(msg["channel"], msg, watching)
    except Exception:
        chat.log.warning("crew: chat notification failed", exc_info=True)


@router.delete("/crew/api/chat/message/{message_id}", dependencies=CHAT)
async def chat_delete(request: Request, message_id: int):
    msg = await asyncio.to_thread(_people_call, chat.remove, request.state.user, message_id)
    chat.hub.publish(msg["channel"], {"type": "delete", "channel": msg["channel"], "id": msg["id"]})
    return {"ok": True}


@router.put("/crew/api/chat/push", dependencies=CHAT)
def chat_push(request: Request, payload: dict):
    _people_call(chat.set_push, request.state.user, payload.get("channel"), payload.get("on"))
    return {"channels": _people_call(chat.channels_for, request.state.user)}


@router.get("/crew/api/chat/stream", dependencies=CHAT)
async def chat_stream(request: Request):
    """Server-sent events for every channel this person may read: {"type": "message" | "delete", ...}. The phone
    reloads the newest messages whenever the stream (re)connects, so nothing has to be replayed."""
    from .. import event_stream, sse
    from .. import modules as shared

    user = request.state.user
    chat.hub.loop = asyncio.get_running_loop()
    sub = chat.hub.subscribe(user, await asyncio.to_thread(chat.own_channel, user.id))

    async def gen():
        try:
            yield sse({"type": "ready"}).encode()
            while True:
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=shared.HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield sse({"type": "tick"}).encode()
                    continue
                if event is None:
                    return
                yield sse(event).encode()
        finally:
            chat.hub.unsubscribe(sub)

    return event_stream(gen())
