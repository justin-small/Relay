"""The crew page's routes. Mounted by the module registry, so each answers 404 while the module is off, and the
registry's panel-only rule (Module.panel_prefixes) answers 404 for every /crew path on the viewer port.

Public on purpose, because a browser fetches them without the sign-in cookie: the manifest, the service worker and
its offline page. They carry nothing but the page's name and icons."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ... import audit, config, db, directory, netroute, schedules, serving, tls
from .. import listener_port, modules as registry, require, using
from . import calltimes, chat, checkin, checklists, devices, paging, people, resources

router = APIRouter()
VIEW = [Depends(require("crew.view"))]
MANAGE = [Depends(require("crew.manage"))]
PAGE = [Depends(require("crew.page"))]
USE = [Depends(require("crew.use"))]
CHAT = [Depends(require("crew.chat"))]
CHECKIN = [Depends(require("crew.checkin"))]
CHECKLIST = [Depends(require("crew.checklist"))]
RESOURCES = [Depends(require("crew.resources"))]
ACTIVITY = [Depends(require("crew.activity"))]
CHECKLIST_EDIT = [Depends(require("crew.checklist_edit"))]

KEY = "crew"
HERE = Path(__file__).resolve().parent
NO_STORE = {"Cache-Control": "no-store"}
ICONS = [
    {"src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
]



def _log(request: Request, action: str, summary: str, *, target="", detail: dict | None = None) -> None:
    """Enter what a crew member did from the crew page (#235). The audit middleware only sees /api/admin, so the
    /crew/api routes write their own entry, after the work succeeded. Never put a message body or a position in
    `detail`: only who did what, and to which thing."""
    audit.record(action, summary, actor=request.state.user, target=target, detail=detail,
                 ip=request.client.host if request.client else "")


def _settings() -> dict:
    return config.get()[KEY]


def _pkg_module():
    from . import MODULE

    return MODULE


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
        role = role["crew_role"]
    except Exception:
        role = ""      # the role is a label: the page works without it
    return templates.TemplateResponse(request, "crew/crew.html", {
        "app_name": s["name"], "show_plan": s["show_plan"], "user": got, "crew_role": role,
        "tools": registry.user_nav(got), "tabs": registry.crew_nav(got),
        "module_scripts": registry.crew_scripts(got),
        "module_styles": registry.crew_styles(got),
    }, headers=NO_STORE)


def _join_addresses(host: str | None = None) -> dict:
    """The two addresses of the crew page for a phone (#236): the HTTPS one, on Relay's current LAN address and the
    HTTPS port (not the viewer port), and the plain-HTTP join page the QR code points at, which explains the
    certificate first. `host` is what the phone itself reached, when it is the one asking."""
    ips = netroute.link_addresses("crew")   # the crew network's address when the crew pages are routed (#346)
    where = host or (ips[0] if ips else "")
    if ":" in where and not where.startswith("["):
        where = f"[{where}]"
    join_port = serving.listener_bound(("/crew-join",)) or listener_port(_settings(), _pkg_module().listeners[0])
    return {"host": where, "crew_url": serving.link("https", where, serving.admin_port(), "/crew") if where else "",
            "join_url": serving.link("http", where, join_port, "/crew-join") if where else ""}


@router.get("/api/admin/crew/join", dependencies=VIEW)
def join_get():
    return _join_addresses()


@router.get("/crew-join", response_class=HTMLResponse)
def join_page(request: Request):
    """Public, on the join port only: how to trust Relay's certificate, then on to the crew page. Plain HTTP on
    purpose: a phone that does not trust the certificate cannot show an HTTPS page, only a browser warning."""
    _, templates = _pkg()
    raw = request.headers.get("host") or ""
    host = raw if raw.endswith("]") or raw.count(":") != 1 else raw.split(":", 1)[0]
    addr = _join_addresses(host or None)
    return templates.TemplateResponse(request, "crew/join.html", {
        "app_name": _settings()["name"], "crew_url": addr["crew_url"], "have_cert": tls.cert_path().is_file(),
    }, headers=NO_STORE)


@router.get("/crew-join/relay.crt")
def join_cert():
    """Relay's certificate, which is public (the key never leaves the machine), for a phone to install."""
    path = tls.cert_path()
    if not path.is_file():
        raise HTTPException(404, "Relay has no certificate yet.")
    return Response(path.read_bytes(), media_type="application/x-x509-ca-cert", headers={
        "Content-Disposition": 'attachment; filename="relay.crt"', "Cache-Control": "no-cache"})


@router.get("/crew-join/mark.svg")
def join_mark():
    """Relay's mark for the join page, which is on its own port and cannot reach /static."""
    path = HERE.parent.parent / "static" / "brand" / "relay-mark.svg"
    return Response(path.read_bytes(), media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


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
    rundown_now = using("plan.rundown_now")
    if not _settings()["show_plan"] or rundown_now is None:
        return {"available": False, "plan": None}
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


@router.get("/crew/api/today", dependencies=USE)
def crew_today(request: Request):
    """The booth-off screen (#234): is capture running, and this person's call time and position. Only the
    person's own teams and next call time; nothing about anyone else."""
    from ...engine import engine

    user = request.state.user
    tracks, zone = calltimes._state()
    try:
        teams = checklists.teams_of(user.id, tracks)
        call = calltimes.next_for(user.id, tracks, zone)
        role = people.profile(user.id)["crew_role"]
    except paging.Invalid:
        teams, call, role = [], None, ""
    return {"running": bool(engine.running), "teams": teams, "role": role, "zone": str(zone) if zone else None,
            "call": call}


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
    try:
        fence = checkin.clean_geofence(payload.get("geofence"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    hosts = payload.get("embed_hosts", _settings().get("embed_hosts"))
    if "embed_hosts" in payload and not isinstance(hosts, list):
        raise HTTPException(400, "Give the allowed embed sites as a list.")
    clean = MODULE.clean({"name": name, "show_plan": show, "geofence": fence, "embed_hosts": hosts})
    config.save({KEY: clean}, replace=(KEY,))
    audit.note(request, "crew.change", "Changed the crew page's setup", detail={"show_plan": clean["show_plan"], "geofence": clean["geofence"] is not None})
    return _settings()


# -- roles and Planning Center names (#227) --------------------------------------------------------------------
def _teams() -> list[dict]:
    """The Planning Center teams an admin page picks from: {"ref", "name", "missing"}."""
    return directory.teams()


def _picks() -> list[dict]:
    """Teams, then each team's positions, for a page that picks one: {"ref", "label"}. A team is for everyone on it, a
    position for the people in that position only."""
    out = [{"ref": t["ref"], "label": t["name"]} for t in directory.teams() if not t["missing"]]
    out += [{"ref": p["ref"], "label": f"{p['team']} \u00b7 {p['name']}" if p["team"] else p["name"]}
            for p in directory.positions() if not p["missing"]]
    return out


def _lead_picks(user_id: int) -> list[dict]:
    """What a team lead may pick: only the teams and positions they lead ("name" is for one saved with no ref)."""
    return [{"ref": l["ref"], "label": l["name"], "name": l["name"]} for l in checklists.lead_rows(user_id)]


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
    role = _people_call(people.set_role, user_id, payload.get("crew_role"))
    chat.hub.signed_out(user_id)         # their role channel changed: their open chat reconnects and reloads
    audit.note(request, "crew.role", "Set a crew role", target=user_id, detail={"crew_role": role})
    return {"crew_role": role}


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
    follower = using("plan.follower")
    if follower is None:
        return []
    return list(follower.tracks.values())


@router.get("/crew/api/activity", dependencies=ACTIVITY)
def activity(actor: str | None = None, kind: str | None = None, days: int | None = None, before: int | None = None):
    """The crew activity trail (#235): the crew entries of the audit log, newest first. Shares its storage and
    its 90-day retention; only people who hold crew.activity read it."""
    kind = f"crew.{kind}" if kind and "." not in kind else (kind if kind and kind.startswith("crew.") else None)
    who = actor if actor == "system" else (int(actor) if actor and actor.isdigit() else None)
    found = audit.search(actor=who, kind=kind or "crew", days=days, before=before)
    people = db.query("select a.actor_id as id, a.actor_name as name from audit_log a join (select actor_id,"
                      " max(id) as last from audit_log where action like 'crew.%' and actor_id is not null"
                      " group by actor_id) m on a.id = m.last order by lower(a.actor_name)")
    return {**found, "people": people}


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
        "sender": (row or {}).get("sender") or "", "sent_ts": int((row or {}).get("sent_ts") or 0),
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
    return {"rules": _people_call(calltimes.list_rules), "teams": _people_call(_teams),
            "schedules": [{"ref": schedules.ref(x["id"]), "name": x["name"]} for x in config.get()["schedules"]],
            "planning_center": registry.enabled("planning_center")}


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
    _log(request, "crew.chat_send", "Sent a chat message", target=msg["channel"])
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
    _log(request, "crew.chat_delete", "Deleted a chat message", target=msg["channel"],
         detail={"own": msg.get("user_id") == request.state.user.id})
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


# -- check-in and the arrival board (#231) ------------------------------------------------------------------------
def _plan_key() -> str:
    tracks, zone = calltimes._state()
    return checkin.plan_key(tracks, zone, __import__("time").time())


def _my_checkin(user, request: Request) -> dict:
    key = _plan_key()
    row = checkin.mine(user.id, key)
    return {"arrived": row is not None, "ts": row["ts"] if row else None, "method": row["method"] if row else "",
            "lan": checkin.is_lan(request.client.host if request.client else ""),
            "geofence": _settings().get("geofence") is not None}


@router.get("/crew/api/checkin", dependencies=CHECKIN)
def checkin_state(request: Request):
    return _people_call(_my_checkin, request.state.user, request)


@router.post("/crew/api/checkin", dependencies=CHECKIN)
def checkin_now(request: Request, payload: dict):
    """Mark the signed-in person arrived: from the church network, or with a position inside the geofence. The
    position is only compared with the church's and then dropped."""
    user = request.state.user
    host = request.client.host if request.client else ""
    if checkin.is_lan(host):
        method = "network"
    elif checkin.inside(_settings().get("geofence"), payload.get("lat"), payload.get("lon"), payload.get("accuracy")):
        method = "geofence"
    else:
        raise HTTPException(403, "Relay cannot tell you are at the church.")
    _people_call(checkin.arrive, user.id, _plan_key(), method)
    _log(request, "crew.checkin", "Checked in at the church", detail={"method": method})
    return _people_call(_my_checkin, user, request)


@router.get("/api/admin/crew/arrivals", dependencies=PAGE)
def arrivals_get():
    tracks, zone = calltimes._state()
    return _people_call(checkin.board, tracks, zone)


@router.delete("/api/admin/crew/arrivals", dependencies=PAGE)
def arrivals_clear(request: Request):
    n = _people_call(checkin.clear, _plan_key())
    audit.note(request, "crew.arrivals_clear", "Cleared the arrival board", detail={"people": n})
    return arrivals_get()


# -- Wi-Fi check-in: a person's own devices, seen by the UniFi module (#356) ---------------------------------------
def _wifi_client(request: Request) -> dict | None:
    """The UniFi client this request came from, when UniFi is on and sees the address."""
    service = using("unifi.service")
    if service is None:
        return None
    return service.presence.by_ip(request.client.host if request.client else "")


def _device_view(row: dict) -> dict:
    return {"id": row["id"], "kind": row["kind"], "value": row["value"], "label": row["label"],
            "added_by": row["added_by"]}


def _my_devices(user, request: Request) -> dict:
    mine = [_device_view(r) for r in devices.mine(user.id)]
    here = _wifi_client(request)
    this = None
    if here:
        owned = any(d["kind"] == "mac" and d["value"] == here["mac"] for d in mine)
        this = {"name": here.get("name") or here.get("hostname") or "", "hostname": here.get("hostname") or "",
                "private": bool(here.get("private")), "yours": owned}
    return {"devices": mine, "wifi": registry.running("unifi"), "this": this}


@router.get("/crew/api/devices", dependencies=CHECKIN)
def devices_get(request: Request):
    return _people_call(_my_devices, request.state.user, request)


@router.post("/crew/api/devices/this", dependencies=CHECKIN)
def devices_this(request: Request, payload: dict):
    """"This is my phone": add the network client the request came from, and check in now."""
    user = request.state.user
    here = _wifi_client(request)
    if here is None:
        raise HTTPException(409, "Relay cannot see this phone on the church Wi-Fi. Join the Wi-Fi and try again.")
    _people_call(devices.add, user.id, "mac", here["mac"], payload.get("label") or here.get("name") or "")
    tracks, zone = calltimes._state()
    now = __import__("time").time()
    key = checkin.plan_key(tracks, zone, now)
    if _people_call(checkin.mine, user.id, key) is None:
        _people_call(checkin.arrive, user.id, key, "wifi", now)
    _log(request, "crew.device_add", "Added a phone for Wi-Fi check-in", detail={"kind": "mac", "how": "this"})
    return _people_call(_my_devices, user, request)


@router.post("/crew/api/devices", dependencies=CHECKIN)
def devices_add(request: Request, payload: dict):
    user = request.state.user
    _people_call(devices.add, user.id, payload.get("kind"), payload.get("value"), payload.get("label"))
    _log(request, "crew.device_add", "Added a device for Wi-Fi check-in", detail={"kind": payload.get("kind")})
    return _people_call(_my_devices, user, request)


@router.delete("/crew/api/devices/{device_id}", dependencies=CHECKIN)
def devices_remove(request: Request, device_id: int):
    user = request.state.user
    if not _people_call(devices.remove, device_id, user.id):
        raise HTTPException(404, "That device is not on your list.")
    _log(request, "crew.device_remove", "Removed a device from Wi-Fi check-in")
    return _people_call(_my_devices, user, request)


@router.get("/api/admin/crew/devices", dependencies=VIEW)
def admin_devices():
    return {"devices": _people_call(devices.everyone), "wifi": registry.running("unifi")}


@router.post("/api/admin/crew/devices", dependencies=MANAGE)
def admin_devices_add(request: Request, payload: dict):
    uid = payload.get("user_id")
    if isinstance(uid, bool) or not isinstance(uid, int) or not db.one("select id from users where id = ?", (uid,)):
        raise HTTPException(400, "Pick a person.")
    row = _people_call(devices.add, uid, payload.get("kind"), payload.get("value"), payload.get("label"), "admin")
    audit.note(request, "crew.device_add", "Added a device for Wi-Fi check-in", target=uid,
               detail={"kind": row["kind"]})
    return admin_devices()


@router.delete("/api/admin/crew/devices/{device_id}", dependencies=MANAGE)
def admin_devices_remove(request: Request, device_id: int):
    if not _people_call(devices.remove, device_id):
        raise HTTPException(404, "No such device.")
    audit.note(request, "crew.device_remove", "Removed a device from Wi-Fi check-in", target=device_id)
    return admin_devices()


# -- checklists per position (#232) -------------------------------------------------------------------------------
def _my_checklists(user) -> dict:
    tracks, _ = calltimes._state()
    key = _plan_key()
    editable = user.can("crew.checklist_edit")
    return {"plan": key, "lists": checklists.mine(user.id, key, tracks), "editor": editable or bool(checklists.leads_of(user.id)),
            "all": editable}


@router.get("/crew/api/checklists", dependencies=CHECKLIST)
def checklists_mine(request: Request):
    return _people_call(_my_checklists, request.state.user)


@router.put("/crew/api/checklists/item/{item_id}", dependencies=CHECKLIST)
def checklists_tick(request: Request, item_id: int, payload: dict):
    tracks, _ = calltimes._state()
    _people_call(checklists.tick, request.state.user.id, item_id, payload.get("done"), _plan_key(), tracks)
    _log(request, "crew.checklist_tick", "Ticked a checklist item" if payload.get("done") else "Unticked a checklist item",
         target=item_id)
    return _people_call(_my_checklists, request.state.user)


def _editable(user) -> list[dict]:
    return [t for t in checklists.templates() if checklists.can_edit(user, t["position"], "crew.checklist_edit", t["position_ref"])]


@router.get("/crew/api/checklists/templates", dependencies=CHECKLIST)
def checklists_templates(request: Request):
    """The templates this person may edit: all of them for an operator, their own positions' for a team lead."""
    user = request.state.user
    tracks, _ = calltimes._state()
    out = _people_call(_editable, user)
    if not out and not user.can("crew.checklist_edit") and not _people_call(checklists.leads_of, user.id):
        raise HTTPException(403, "You cannot edit checklists.")
    return {"templates": out, "picks": _picks() if user.can("crew.checklist_edit") else _lead_picks(user.id), "all": user.can("crew.checklist_edit"),
            "leads": _people_call(checklists.leads_of, user.id)}


def _check_edit(user, position: str, position_ref: str = "") -> None:
    if not _people_call(checklists.can_edit, user, position, "crew.checklist_edit", position_ref):
        raise HTTPException(403, "You can edit checklists only for the positions you lead." if position or position_ref
                            else "Only an operator edits the checklist for everyone.")


@router.post("/crew/api/checklists/templates", dependencies=CHECKLIST)
def checklists_add(request: Request, payload: dict):
    data = _people_call(checklists.clean, payload)
    _check_edit(request.state.user, data["position"], data["position_ref"])
    lid = _people_call(checklists.save, data)
    _log(request, "crew.checklist_add", "Added a crew checklist", target=lid, detail={"position": data["position"]})
    return checklists_templates(request)


@router.put("/crew/api/checklists/templates/{list_id}", dependencies=CHECKLIST)
def checklists_change(request: Request, list_id: int, payload: dict):
    data = _people_call(checklists.clean, payload)
    old = _people_call(checklists.get, list_id)
    if old is None:
        raise HTTPException(404, "No such checklist.")
    _check_edit(request.state.user, old["position"], old["position_ref"])   # a lead may not take over someone else's list
    _check_edit(request.state.user, data["position"], data["position_ref"])  # nor move their own onto another position
    _people_call(checklists.save, data, list_id)
    _log(request, "crew.checklist_change", "Changed a crew checklist", target=list_id, detail={"position": data["position"]})
    return checklists_templates(request)


@router.delete("/crew/api/checklists/templates/{list_id}", dependencies=CHECKLIST)
def checklists_delete(request: Request, list_id: int):
    old = _people_call(checklists.get, list_id)
    if old is None:
        raise HTTPException(404, "No such checklist.")
    _check_edit(request.state.user, old["position"], old["position_ref"])
    _people_call(checklists.delete, list_id)
    _log(request, "crew.checklist_delete", "Removed a crew checklist", target=list_id, detail={"position": old["position"]})
    return checklists_templates(request)


@router.get("/api/admin/crew/checklists", dependencies=PAGE)
def checklists_board(plan: str | None = None):
    tracks, zone = calltimes._state()
    return _people_call(checklists.board, tracks, zone, plan or None)


@router.get("/api/admin/crew/checklist-leads", dependencies=CHECKLIST_EDIT)
def checklist_leads():
    return {"leads": _people_call(checklists.list_leads), "picks": _picks(), "people": [
        {"id": p["id"], "name": p["name"]} for p in _people_call(people.people_list) if not p["disabled"]]}


@router.post("/api/admin/crew/checklist-leads", dependencies=CHECKLIST_EDIT)
def checklist_leads_add(request: Request, payload: dict):
    _people_call(checklists.add_lead, payload.get("user_id"), payload.get("position"),
                 payload.get("position_ref"))
    audit.note(request, "crew.checklist_lead", "Made someone a checklist lead", target=payload.get("user_id"),
               detail={"position": payload.get("position")})
    return checklist_leads()


@router.delete("/api/admin/crew/checklist-leads/{user_id}", dependencies=CHECKLIST_EDIT)
def checklist_leads_remove(request: Request, user_id: int, position: str = "", position_ref: str = ""):
    _people_call(checklists.remove_lead, user_id, position, position_ref)
    audit.note(request, "crew.checklist_lead_remove", "Removed a checklist lead", target=user_id, detail={"position": position})
    return checklist_leads()


# -- resources per position (#233) --------------------------------------------------------------------------------
def _hosts() -> list[str]:
    return resources.clean_hosts(_settings().get("embed_hosts"))


def _my_resources(user) -> dict:
    tracks, _ = calltimes._state()
    operator = user.can("crew.resources_edit")
    return {"resources": resources.mine(user.id, tracks), "editor": operator or bool(checklists.leads_of(user.id)),
            "all": operator}


@router.get("/crew/api/resources", dependencies=RESOURCES)
def resources_mine(request: Request):
    return _people_call(_my_resources, request.state.user)


def _res_edit(user, pick: tuple[str, str]) -> None:
    ref, position = pick
    if not _people_call(checklists.can_edit, user, position, "crew.resources_edit", ref):
        raise HTTPException(403, "You can add resources only for the positions you lead." if position or ref
                            else "Only an operator adds a resource for everyone.")


@router.get("/crew/api/resources/manage", dependencies=RESOURCES)
def resources_manage(request: Request):
    user = request.state.user
    tracks, _ = calltimes._state()
    leads = _people_call(checklists.leads_of, user.id)
    if not user.can("crew.resources_edit") and not leads:
        raise HTTPException(403, "You cannot edit resources.")
    rows = _people_call(resources.all_rows)
    return {"resources": [r for r in rows if checklists.can_edit(user, r["position"], "crew.resources_edit", r["position_ref"])],
            "picks": _picks() if user.can("crew.resources_edit") else _lead_picks(user.id),
            "all": user.can("crew.resources_edit"), "leads": leads,
            "embed_hosts": _hosts(), "limit": resources.FILE_LIMIT}


@router.post("/crew/api/resources/link", dependencies=RESOURCES)
def resources_add_link(request: Request, payload: dict):
    _res_edit(request.state.user, _people_call(resources.clean_position, payload.get("position"),
                                               payload.get("position_ref")))
    rid = _people_call(resources.add_url, request.state.user.id, payload.get("kind"), payload.get("title"),
                       payload.get("position"), payload.get("url"), _hosts(), payload.get("position_ref"))
    _log(request, "crew.resource_add", "Added a crew resource", target=rid, detail={"kind": payload.get("kind")})
    return resources_manage(request)


@router.post("/crew/api/resources/file", dependencies=RESOURCES)
async def resources_add_file(request: Request, title: str = "", position: str = "", position_ref: str = ""):
    """A file, sent as the raw request body. The position is checked before any of it is read."""
    user = request.state.user
    pos = _people_call(resources.clean_position, position, position_ref)
    _res_edit(user, pos)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > resources.FILE_LIMIT + 1024:
        raise HTTPException(413, "That file is too large. Files can be up to 25 MB.")
    rid = await _people_call_async(resources.add_file, user.id, title, pos[1], request.stream(), pos[0])
    _log(request, "crew.resource_add", "Added a crew resource", target=rid, detail={"kind": "file"})
    return await asyncio.to_thread(resources_manage, request)


async def _people_call_async(fn, *args):
    from ... import db

    try:
        return await fn(*args)
    except people.Invalid as exc:
        raise HTTPException(exc.status, exc.message)
    except db.Unavailable:
        raise HTTPException(503, "The user database is not available.")


@router.put("/crew/api/resources/{rid}", dependencies=RESOURCES)
def resources_change(request: Request, rid: int, payload: dict):
    old = _people_call(resources.get, rid)
    if old is None:
        raise HTTPException(404, "No such resource.")
    _res_edit(request.state.user, (old["position_ref"], old["position"]))
    _res_edit(request.state.user, _people_call(resources.clean_position, payload.get("position"),
                                               payload.get("position_ref")))
    _people_call(resources.change, rid, payload.get("title"), payload.get("position"), payload.get("url"), _hosts(),
                 payload.get("position_ref"))
    _log(request, "crew.resource_change", "Changed a crew resource", target=rid)
    return resources_manage(request)


@router.delete("/crew/api/resources/{rid}", dependencies=RESOURCES)
def resources_delete(request: Request, rid: int):
    old = _people_call(resources.get, rid)
    if old is None:
        raise HTTPException(404, "No such resource.")
    _res_edit(request.state.user, (old["position_ref"], old["position"]))
    _people_call(resources.delete, rid)
    _log(request, "crew.resource_delete", "Removed a crew resource", target=rid, detail={"kind": old["kind"]})
    return resources_manage(request)


@router.get("/crew/api/resources/{rid}/file", dependencies=RESOURCES)
def resources_file(request: Request, rid: int):
    """A stored file, for a person it is shared with or who may edit its position. Pictures and text are shown with
    a locked-down policy; PDFs show in the browser; Office files download."""
    user = request.state.user
    row = _people_call(resources.get, rid)
    tracks, _ = calltimes._state()
    if row is None or row["kind"] != "file" or not (
            _people_call(resources.sees, user.id, rid, tracks)
            or _people_call(checklists.can_edit, user, row["position"], "crew.resources_edit", row["position_ref"])):
        raise HTTPException(404, "No such file.")
    path = resources.file_path(row)
    if path is None:
        raise HTTPException(404, "That file is no longer on this machine.")
    mime, inline = resources.TYPES[row["ext"]]
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if row["ext"] != "pdf":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    name = "".join(c if c.isalnum() or c in " ._-" else "_" for c in row["title"]).strip() or "file"
    return FileResponse(path, media_type=mime, headers=headers, filename=f"{name}.{row['ext']}",
                        content_disposition_type="inline" if inline else "attachment")
