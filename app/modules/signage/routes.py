"""Signage routes (#205).

Public, on the signage port only (`Listener` in __init__.py; core refuses /signage on every other port):

    GET  /signage/<slug>                 the full-screen player page
    GET  /signage/_/player.js            its script
    GET  /signage/_/state/<slug>         what the screen plays now (JSON)
    GET  /signage/_/events/<slug>        the same, pushed whenever it changes (server-sent events)
    POST /signage/_/now/<slug>           the player saying which item it is showing
    GET  /signage/_/media/<sha>.<ext>    an uploaded file, cached by the browser for good

Admin, on the panel port, each behind signage.view or signage.manage.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ... import audit, boards, config, db, netroute, schedules, serving
from ... import screens as core_screens
from .. import HEARTBEAT_S, event_stream, read_limited, require, sse
from . import live, media, sources, store, takeover

HERE = Path(__file__).resolve().parent
PLAYER_JS = HERE / "static" / "player.js"
MEDIA_PREFIX = "/signage/_/media/"
IMMUTABLE = "public, max-age=31536000, immutable"
VIEW = [Depends(require("signage.view"))]
MANAGE = [Depends(require("signage.manage"))]

router = APIRouter()


def _templates():
    from .. import templates
    return templates


def _refuse(exc: Exception) -> JSONResponse:
    if isinstance(exc, (store.Invalid, media.Refused)):
        return JSONResponse({"error": exc.message}, status_code=exc.status)
    if isinstance(exc, db.Unavailable):
        return JSONResponse({"error": "The database is not available right now. Try again in a moment."},
                            status_code=503)
    raise exc


def _int(v: str) -> int:
    try:
        return int(v)
    except ValueError:
        raise HTTPException(404, "Not Found") from None


# ================================================================== public (signage port)

def _screen_or_404(slug: str) -> dict:
    try:
        screen = store.screen_by_slug(slug)
    except db.Unavailable:
        raise HTTPException(503, "Signage is not available right now") from None
    if screen is None:
        raise HTTPException(404, "Not Found")
    return screen


def _state(screen: dict) -> dict:
    """What a player needs. The screen's core record (#527) can decide it shows a board instead, or a playlist of its
    own choosing: that is the `board` redirect and the `playlist_id` the schedule layers sit on."""
    core = core_screens.get(screen["core_id"])
    shows = core_screens.showing_now(core, signage_on=True) if core else {"kind": ""}
    if shows["kind"] == "board" and shows["ref"] and boards.find(config.get()["boards"], shows["ref"]):
        return {"screen": {"slug": screen["slug"], "name": screen["name"], "sound": bool(screen["sound"])},
                "playlist": None, "items": [], "today": "", "server_time": int(time.time() * 1000),
                "takeover": None, "banner": None,
                "board": {"port": serving.viewer_port(config.get()["port"]),
                          "path": f"/board/{shows['ref']}?screen=screen-{core['id']}"}}
    if shows["kind"] == "signage" and shows["ref"].isdigit() and store.playlist_get(int(shows["ref"])):
        screen = {**screen, "playlist_id": int(shows["ref"]), "group_id": None}
    out = store.player_state(screen)
    out["takeover"] = takeover.payload(screen)
    out["banner"] = takeover.banner(screen)
    out["server_time"] = int(time.time() * 1000)
    return out


@router.get("/signage/_/player.js")
def player_js():
    return FileResponse(PLAYER_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/signage/_/media/{name}")
def public_media(name: str):
    p = media.file_path(name)
    if p is None:
        raise HTTPException(404, "Not Found")
    return FileResponse(p, media_type=media.TYPES[p.suffix[1:]][1], headers={"Cache-Control": IMMUTABLE})


@router.get("/signage/_/state/{slug}")
def public_state(slug: str):
    return JSONResponse(_state(_screen_or_404(slug)), headers={"Cache-Control": "no-store"})


@router.get("/signage/_/events/{slug}")
async def public_events(slug: str, request: Request):
    _screen_or_404(slug)

    async def gen():
        last_sent = None
        last_minute = int(time.time() // 60)
        seen = -1
        beat = time.monotonic()
        alert_key = takeover.alert_sig()
        while not await request.is_disconnected():
            # Alerts are Relay's own and change without anything in signage being edited: look each second.
            key_now = takeover.alert_sig()
            if key_now != alert_key:
                alert_key = key_now
                seen = -1
            if live.version != seen:
                seen = live.version
                try:
                    screen = await asyncio.to_thread(store.screen_by_slug, slug)
                    payload = await asyncio.to_thread(_state, screen) if screen else {"gone": True}
                except db.Unavailable:
                    payload = None
                if payload is not None:
                    key = json.dumps({k: v for k, v in payload.items() if k != "server_time"}, sort_keys=True)
                    if key != last_sent:
                        last_sent = key
                        beat = time.monotonic()
                        yield sse(payload)
            if time.monotonic() - beat >= HEARTBEAT_S:
                beat = time.monotonic()
                yield ": keepalive\n\n"
            # The clock can change what plays (a slot starts or ends, an override runs out, the date turns): look
            # again each minute without anything being edited.
            minute = int(time.time() // 60)
            if minute != last_minute:
                last_minute = minute
                seen = -1
            await asyncio.sleep(1.0)

    return event_stream(gen())


@router.post("/signage/_/now/{slug}")
async def public_now(slug: str, request: Request):
    _screen_or_404(slug)
    try:
        body = json.loads(await read_limited(request, 1024) or b"{}")
    except ValueError:
        raise HTTPException(400, "Bad request") from None
    item = body.get("item") if isinstance(body, dict) else None
    item = item if isinstance(item, int) else None
    label = body.get("label") if isinstance(body, dict) and isinstance(body.get("label"), str) else ""
    live.report(slug, item, label)
    core = core_screens.by_key(store.SCREEN_KEY + slug)
    if core:
        core_screens.touch(core["id"], address=request.client.host if request.client else "")
    return {"ok": True}


@router.post("/signage/_/done/{slug}")
async def public_done(slug: str, request: Request):
    """The player saying it has played a "once" push to its end, so this screen goes back to its playlist."""
    _screen_or_404(slug)
    try:
        body = json.loads(await read_limited(request, 256) or b"{}")
    except ValueError:
        raise HTTPException(400, "Bad request") from None
    pid = body.get("id") if isinstance(body, dict) else None
    if isinstance(pid, int) and not isinstance(pid, bool):
        live.done(slug, pid)
    return {"ok": True}


@router.get("/signage/{slug}")
def player_page(slug: str, request: Request):
    screen = _screen_or_404(slug)
    v = int(PLAYER_JS.stat().st_mtime)
    return _templates().TemplateResponse(request, "signage/player.html", {
        "screen": screen, "player_js": f"/signage/_/player.js?v={v}",
    }, headers={"Cache-Control": "no-cache", "Referrer-Policy": "no-referrer"})


# ================================================================== admin

def _settings() -> dict:
    return config.get()["signage"]


def _port_info() -> dict:
    from . import MODULE

    configured = int(_settings()["port"])
    bound = serving.listener_bound(MODULE.listeners[0].prefixes)
    return {"configured": configured, "bound": bound, "restart": serving.published() and bound != configured}


def ndi_outputs() -> list[dict]:
    """Each signage screen as a page the NDI module can send (provided as "ndi.outputs.signage"): the player on this
    machine's signage port, so a TV fed by NDI plays the same loop as one with a browser."""
    info = _port_info()
    port = info["bound"] or info["configured"]
    return [{"id": "signage:" + s["slug"], "name": "Signage (" + s["name"] + ")", "group": "Signage",
             "url": f"http://127.0.0.1:{port}" + store.player_path(s["slug"]), "alpha": False} for s in store.screens()]


def _screen_urls(slug: str) -> list[str]:
    info = _port_info()
    port = info["bound"] or info["configured"]
    from . import MODULE

    ips = netroute.link_addresses(MODULE.listeners[0].traffic)   # the screens' network when routed (#346)
    return [serving.link("http", ip, port, f"/signage/{slug}") for ip in ips]


@router.get("/api/admin/signage", dependencies=VIEW)
def admin_state():
    try:
        screens = store.screens()
        lists = store.playlists()
        grps = store.groups()
        lib = store.media_list()
    except db.Unavailable as exc:
        return _refuse(exc)
    names = {p["id"]: p["name"] for p in lists}
    group_names = {g["id"]: g for g in grps}
    items_by_id = {it["id"]: it for p in lists for it in p["items"]}
    now = time.time()
    try:
        slot_rows, over = store.slots(), store.overrides()
    except db.Unavailable as exc:
        return _refuse(exc)
    try:
        taken = takeover.rows()
    except db.Unavailable as exc:
        return _refuse(exc)
    for s in screens:
        plan = store.resolve(s, _slots=slot_rows, _overrides=over)
        tk = takeover.pick(s, taken)
        s["taken_over"] = {"kind": tk["kind"], "label": tk["label"], "from": tk["target"],
                           "until": tk["expires_at"]} if tk else None
        s["banner"] = takeover.banner(s)
        pid = plan["playlist_id"]
        s["playing_playlist"] = {"id": pid, "name": names.get(pid, ""), "from": plan["layer"],
                                 "why": plan["why"], "until": plan["until"]} if pid else None
        if not pid:
            s["why"] = plan["why"]
        s["urls"] = _screen_urls(s["slug"])
        rep = live.playing(s["slug"])
        if rep:
            it = items_by_id.get(rep["item_id"]) if rep["item_id"] else None
            s["now"] = {"label": (it or {}).get("name") or rep["label"], "thumb": (it or {}).get("thumb", ""),
                        "for_s": int(now - rep["since"]), "seen_s": int(now - rep["seen"])}
        else:
            s["now"] = None
        s["group_name"] = (group_names.get(s["group_id"]) or {}).get("name", "")
    return {
        "settings": _settings(),
        "port": _port_info(),
        "screens": screens,
        "groups": grps,
        "playlists": lists,
        "media": lib,
        "storage": media.disk(),
        "limits": {"image": media.IMAGE_LIMIT, "video": media.VIDEO_LIMIT},
        "sources": [{"kind": s.kind, "label": s.label} for s in sources.kinds()],
        "slots": slot_rows,
        "overrides": over,
        "takeovers": taken,
        "views": [{"id": k, "label": v[0]} for k, v in takeover.VIEWS.items()],
        "timezone": config.timezone(),
        "timezones": [{"id": z, "label": l} for z, l in schedules.US_TIMEZONES],
        "named_schedules": [{"ref": schedules.ref(s["id"]), "name": s["name"], "summary": schedules.summary(s)}
                            for s in config.get()["schedules"]],
        "today": store.today(),
    }


@router.get("/api/admin/signage/preview", dependencies=VIEW)
def admin_preview(screen: str = "", playlist: int = 0):
    """What a screen (or a playlist) plays, for the admin page's preview. Media is fetched through the admin
    route, since the signage port's addresses are plain HTTP and the panel page is HTTPS."""
    try:
        if screen:
            s = store.screen_by_slug(screen)
            if s is None:
                raise HTTPException(404, "Not Found")
            state = store.player_state(s)
        else:
            p = store.playlist_get(playlist)
            if p is None:
                raise HTTPException(404, "Not Found")
            state = {"screen": {"slug": "", "name": p["name"], "sound": False},
                     "playlist": {"id": p["id"], "name": p["name"], "from": ""},
                     "items": store.playable_items(p["id"], store.today()), "today": store.today()}
    except db.Unavailable as exc:
        return _refuse(exc)
    for it in state["items"]:
        if it["url"].startswith(MEDIA_PREFIX):
            it["url"] = "/api/admin/signage/file/" + it["url"][len(MEDIA_PREFIX):]
    state["server_time"] = int(time.time() * 1000)
    return state


@router.get("/api/admin/signage/sources/{kind}", dependencies=VIEW)
def admin_source_choices(kind: str):
    src = sources.get(kind)
    if src is None:
        raise HTTPException(404, "Not Found")
    try:
        return {"choices": list(src.choices() or [])[:500]}
    except Exception:
        return JSONResponse({"error": f"{src.label} could not list what it offers."}, status_code=502)


@router.put("/api/admin/signage/settings", dependencies=MANAGE)
def admin_settings(request: Request, payload: dict):
    change: dict = {}
    if "port" in payload:
        try:
            port = int(payload.get("port"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "The port is a number from 1024 to 65535."}, status_code=400)
        if not 1024 <= port <= 65535:
            return JSONResponse({"error": "The port is a number from 1024 to 65535."}, status_code=400)
        cfg = config.get()
        if port in (serving.viewer_port(cfg["port"]), serving.admin_port(cfg["admin_port"])):
            return JSONResponse({"error": "Relay already uses that port. Choose another."}, status_code=400)
        change["port"] = port
    if "override_until" in payload:
        if payload.get("override_until") not in ("next_change", "cleared"):
            return JSONResponse({"error": "A hand-chosen playlist lasts until the next scheduled change or until cleared."},
                                status_code=400)
        change["override_until"] = payload["override_until"]
    if "alert_screens" in payload:
        if payload.get("alert_screens") not in takeover.ALERT_MODES:
            return JSONResponse({"error": "Alerts show on all screens, on chosen groups, or not at all."},
                                status_code=400)
        change["alert_screens"] = payload["alert_screens"]
    if "alert_groups" in payload:
        ids = payload.get("alert_groups")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return JSONResponse({"error": "Choose groups from the list."}, status_code=400)
        try:
            known = {g["id"] for g in store.groups()}
        except db.Unavailable as exc:
            return _refuse(exc)
        change["alert_groups"] = sorted(set(ids) & known)
    if not change:
        return JSONResponse({"error": "Nothing to change."}, status_code=400)
    config.save({"signage": change})
    live.bump()
    audit.note(request, "signage.settings", "Changed the signage settings", detail=change)
    return {"settings": _settings(), "port": _port_info()}


# ------------------------------------------------------------------ schedule slots and overrides (#206)

@router.post("/api/admin/signage/slots", dependencies=MANAGE)
def admin_slot_create(request: Request, payload: dict):
    try:
        row = store.slot_save(None, payload)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.slot_create", "Added a signage schedule slot", target=str(row.get("id", "")))
    return row


@router.put("/api/admin/signage/slots/{sid}", dependencies=MANAGE)
def admin_slot_update(sid: str, request: Request, payload: dict):
    try:
        row = store.slot_save(_int(sid), payload)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.slot_update", "Changed a signage schedule slot", target=str(row.get("id", "")))
    return row


@router.delete("/api/admin/signage/slots/{sid}", dependencies=MANAGE)
def admin_slot_delete(sid: str, request: Request):
    try:
        store.slot_delete(_int(sid))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.slot_delete", "Deleted a signage schedule slot", target=sid)
    return {"ok": True}


@router.put("/api/admin/signage/override", dependencies=MANAGE)
def admin_override_set(request: Request, payload: dict):
    until = payload.get("until") or _settings().get("override_until", "next_change")
    if until not in ("next_change", "cleared"):
        return JSONResponse({"error": "A hand-chosen playlist lasts until the next scheduled change or until cleared."},
                            status_code=400)
    try:
        row = store.override_set(str(payload.get("target") or ""), payload.get("target_id") or 0,
                                 _int(str(payload.get("playlist_id") or 0)), until)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    except (TypeError, ValueError):
        return JSONResponse({"error": "Choose a screen or a group."}, status_code=400)
    audit.note(request, "signage.override_set", "Put a signage playlist on by hand",
               target=f"{row['target']} {row['target_id']}", detail={"playlist": row["playlist_id"], "until": until})
    return row


@router.delete("/api/admin/signage/override", dependencies=MANAGE)
def admin_override_clear(request: Request, target: str = "", target_id: int = 0):
    try:
        store.override_clear(target if target in ("screen", "group", "all") else "", target_id)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.override_clear", "Cleared a hand-chosen signage playlist",
               target=f"{target} {target_id}")
    return {"ok": True}


# ------------------------------------------------------------------ takeovers (#207)

@router.put("/api/admin/signage/takeover", dependencies=MANAGE)
def admin_takeover_set(request: Request, payload: dict):
    kind = payload.get("kind")
    target = str(payload.get("target") or "")
    try:
        target_id = int(payload.get("target_id") or 0)
        if kind == "push":
            row = takeover.push_set(target, target_id, str(payload.get("media_id") or ""),
                                    str(payload.get("mode") or "cleared"), int(payload.get("seconds") or 0))
        elif kind == "live":
            row = takeover.live_view_set(target, target_id, str(payload.get("view") or ""))
        else:
            return JSONResponse({"error": "Push a file or put a live view on."}, status_code=400)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    except (TypeError, ValueError):
        return JSONResponse({"error": "Choose a screen or a group, and a number of seconds."}, status_code=400)
    audit.note(request, "signage.takeover_set",
               "Pushed a file to signage screens" if kind == "push" else "Put a live view on signage screens",
               target=f"{row['target']} {row['target_id']}",
               detail={"kind": kind, "view": row["view"], "mode": row["mode"], "seconds": row["seconds"]})
    return row


@router.delete("/api/admin/signage/takeover", dependencies=MANAGE)
def admin_takeover_clear(request: Request, target: str = "", target_id: int = 0):
    if target not in ("screen", "group", "all"):
        return JSONResponse({"error": "Choose a screen, a group or all screens."}, status_code=400)
    try:
        takeover.clear(target, target_id)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.takeover_clear", "Returned signage screens to their playlist",
               target=f"{target} {target_id}")
    return {"ok": True}


@router.delete("/api/admin/signage/takeovers", dependencies=MANAGE)
def admin_takeover_clear_all(request: Request):
    try:
        n = takeover.clear_all()
    except db.Unavailable as exc:
        return _refuse(exc)
    audit.note(request, "signage.takeover_clear_all", "Returned every signage screen to its playlist",
               detail={"cleared": n})
    return {"ok": True, "cleared": n}


# ------------------------------------------------------------------ media

@router.post("/api/admin/signage/media", dependencies=MANAGE)
async def admin_media_upload(request: Request, name: str = "", width: int = 0, height: int = 0,
                             duration: float = 0.0):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > media.VIDEO_LIMIT:
        return _refuse(media.Refused("That file is too large. Images can be up to 25 MB and videos up to 500 MB.",
                                     413))
    try:
        store.ready()
        sha, ext, size = await media.receive(request.stream())
        row = await asyncio.to_thread(store.media_add, name or f"Upload.{ext}", sha, ext, size, width, height,
                                      duration)
    except (store.Invalid, media.Refused, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.media_upload", "Uploaded signage media", target=row.get("name", ""))
    return row


@router.put("/api/admin/signage/media/{mid}/thumb", dependencies=MANAGE)
async def admin_media_thumb(mid: str, request: Request):
    data = await read_limited(request, media.THUMB_LIMIT)
    try:
        await asyncio.to_thread(store.media_thumb, mid, data)
    except (store.Invalid, media.Refused, db.Unavailable) as exc:
        return _refuse(exc)
    return {"ok": True}


@router.get("/api/admin/signage/media/{mid}/thumb", dependencies=VIEW)
def admin_media_thumb_get(mid: str):
    try:
        row = store.media_get(mid)
    except db.Unavailable as exc:
        return _refuse(exc)
    p = media.thumb_path(row["sha256"]) if row else None
    if p is None:
        raise HTTPException(404, "Not Found")
    return FileResponse(p, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/admin/signage/file/{name}", dependencies=VIEW)
def admin_media_file(name: str):
    p = media.file_path(name)
    if p is None:
        raise HTTPException(404, "Not Found")
    return FileResponse(p, media_type=media.TYPES[p.suffix[1:]][1], headers={"Cache-Control": "private, max-age=86400"})


@router.patch("/api/admin/signage/media/{mid}", dependencies=MANAGE)
def admin_media_rename(mid: str, request: Request, payload: dict):
    try:
        store.media_rename(mid, payload.get("name"))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.media_rename", "Renamed signage media", target=str(payload.get("name") or ""))
    return {"ok": True}


@router.delete("/api/admin/signage/media/{mid}", dependencies=MANAGE)
def admin_media_delete(mid: str, request: Request):
    try:
        row = store.media_get(mid)
        store.media_delete(mid)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.media_delete", "Deleted signage media", target=(row or {}).get("name", ""))
    return {"ok": True}


# ------------------------------------------------------------------ playlists

@router.post("/api/admin/signage/playlists", dependencies=MANAGE)
def admin_playlist_create(request: Request, payload: dict):
    try:
        row = store.playlist_create(payload.get("name"))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.playlist_create", "Added a signage playlist", target=row.get("name", ""))
    return row


@router.patch("/api/admin/signage/playlists/{pid}", dependencies=MANAGE)
def admin_playlist_rename(pid: str, request: Request, payload: dict):
    try:
        store.playlist_rename(_int(pid), payload.get("name"))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.playlist_rename", "Renamed a signage playlist", target=str(payload.get("name")))
    return {"ok": True}


@router.post("/api/admin/signage/playlists/{pid}/duplicate", dependencies=MANAGE)
def admin_playlist_duplicate(pid: str, request: Request, payload: dict | None = None):
    try:
        row = store.playlist_duplicate(_int(pid), (payload or {}).get("name") or "")
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.playlist_duplicate", "Duplicated a signage playlist", target=row.get("name", ""))
    return row


@router.delete("/api/admin/signage/playlists/{pid}", dependencies=MANAGE)
def admin_playlist_delete(pid: str, request: Request):
    try:
        row = store.playlist_get(_int(pid))
        store.playlist_delete(_int(pid))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.playlist_delete", "Deleted a signage playlist", target=(row or {}).get("name", ""))
    return {"ok": True}


@router.put("/api/admin/signage/playlists/{pid}/items", dependencies=MANAGE)
def admin_playlist_items(pid: str, request: Request, payload: dict):
    try:
        store.playlist_set_items(_int(pid), payload.get("items"))
        row = store.playlist_get(_int(pid))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.playlist_items", "Changed a signage playlist's items",
               target=(row or {}).get("name", ""), detail={"items": len(payload.get("items") or [])})
    return {"ok": True}


# ------------------------------------------------------------------ groups and screens

@router.post("/api/admin/signage/groups", dependencies=MANAGE)
def admin_group_create(request: Request, payload: dict):
    try:
        row = store.group_save(None, payload)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.group_create", "Added a screen group", target=row.get("name", ""))
    return row


@router.put("/api/admin/signage/groups/{gid}", dependencies=MANAGE)
def admin_group_update(gid: str, request: Request, payload: dict):
    try:
        row = store.group_save(_int(gid), payload)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.group_update", "Changed a screen group", target=row.get("name", ""))
    return row


@router.delete("/api/admin/signage/groups/{gid}", dependencies=MANAGE)
def admin_group_delete(gid: str, request: Request):
    try:
        store.group_delete(_int(gid))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.group_delete", "Deleted a screen group", target=gid)
    return {"ok": True}


@router.post("/api/admin/signage/screens", dependencies=MANAGE)
def admin_screen_create(request: Request, payload: dict):
    try:
        row = store.screen_save(None, payload)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.screen_create", "Added a signage screen", target=row.get("slug", ""))
    return row


@router.put("/api/admin/signage/screens/{sid}", dependencies=MANAGE)
def admin_screen_update(sid: str, request: Request, payload: dict):
    try:
        row = store.screen_save(_int(sid), payload)
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.screen_update", "Changed a signage screen", target=row.get("slug", ""))
    return row


@router.delete("/api/admin/signage/screens/{sid}", dependencies=MANAGE)
def admin_screen_delete(sid: str, request: Request):
    try:
        store.screen_delete(_int(sid))
    except (store.Invalid, db.Unavailable) as exc:
        return _refuse(exc)
    audit.note(request, "signage.screen_delete", "Deleted a signage screen", target=sid)
    return {"ok": True}
