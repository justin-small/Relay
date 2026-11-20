"""Audio Mixers admin routes (#150). Mounted by the module registry, so each answers 404 while the module is off.

Reading needs `audio_mixers.view`; anything that moves a desk (mute, fader, name, colour, scene, the name push) needs
`audio_mixers.control`, and the server refuses it (403) otherwise; setting desks up and saving the watchdog's baseline
needs `audio_mixers.manage`. Refusals are fixed sentences, never an exception's text.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit
from .. import require
from .models import COLOURS, FAMILIES, MODELS, TESTED
from .service import MAX_DESKS, clean_settings, save, service, settings

router = APIRouter()
VIEW = [Depends(require("audio_mixers.view"))]
CONTROL = [Depends(require("audio_mixers.control"))]
MANAGE = [Depends(require("audio_mixers.manage"))]
WAIT_S = 10.0
WAIT_TICK_S = 0.25
ACTIONS = ("mute", "level", "name", "colour", "scene")


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


def _models() -> list[dict]:
    return [{"id": m.id, "label": m.label, "family": FAMILIES[m.family], "port": m.port, "tested": m.id in TESTED,
             "names": m.name_len > 0, "note": m.note, "ahnet": m.id == "avantis"} for m in MODELS.values()]


def _state(request: Request) -> dict:
    s = settings()
    user = getattr(request.state, "user", None)
    manage = bool(user and user.can("audio_mixers.manage"))
    return {**service.snapshot(), "settings": s if manage else {"desks": s["desks"], "names": s["names"]},
            "models": _models(), "colours": list(COLOURS), "max_desks": MAX_DESKS}


@router.get("/api/admin/audio_mixers", dependencies=VIEW)
async def admin_state(request: Request):
    return _state(request)


@router.get("/api/admin/audio_mixers/wait", dependencies=VIEW)
async def admin_wait(request: Request, since: int = -1):
    """The state once it differs from version `since`, or after WAIT_S anyway (the page asks again at once)."""
    end = time.monotonic() + WAIT_S
    while service.version == since and time.monotonic() < end:
        await asyncio.sleep(WAIT_TICK_S)  # allow-sleep: a long poll, woken by the mirror's version
    return _state(request)


@router.put("/api/admin/audio_mixers/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    changes: dict = {}
    if "desks" in payload:
        sent = payload["desks"]
        if not isinstance(sent, list) or len(sent) > MAX_DESKS:
            return _bad(f"Send a list of at most {MAX_DESKS} desks.")
        saved = {d["id"] for d in settings()["desks"]}
        desks = []
        for d in sent:
            if not isinstance(d, dict):
                return _bad("Send each desk as an object.")
            if d.get("model") not in MODELS:
                return _bad("Pick the desk's model.")
            host = d.get("host")
            if isinstance(host, str) and host.strip() and not clean_settings({"desks": [d]})["desks"][0]["host"]:
                return _bad("That is not an address: use the desk's IP address, like 192.168.1.70.")
            if not (isinstance(d.get("id"), str) and d["id"] in saved):
                d = {**d, "id": "d" + uuid.uuid4().hex[:7]}
            desks.append(d)
        changes["desks"] = desks
    if isinstance(payload.get("watchdog"), dict):
        changes["watchdog"] = payload["watchdog"]
    if isinstance(payload.get("names"), dict):
        changes["names"] = payload["names"]
    if not changes:
        return _bad("Nothing to save.")
    new = save(changes)
    await service.reload()
    audit.note(request, "audio_mixers.settings", "Changed the audio mixer setup",
               detail={"desks": [f"{d['name']} ({MODELS[d['model']].label}, {d['host'] or 'no address'})"
                                 for d in new["desks"]], "watchdog": new["watchdog"]["on"]})
    return _state(request)


@router.post("/api/admin/audio_mixers/{did}/control", dependencies=CONTROL)
async def admin_control(request: Request, did: str, payload: dict):
    action = payload.get("action")
    if action not in ACTIONS:
        return _bad("That does nothing.")
    d = service.desk(did)
    if d is None:
        return _bad("No such desk.", 404)
    strip = payload.get("strip")
    said = service.control(did, action, strip, payload.get("value"))
    if said:
        return _bad(said, 409)
    shown = d.mirror.strips[strip].shown() if isinstance(strip, str) and strip in d.mirror.strips else ""
    what = {"mute": f"{'muted' if payload.get('value') else 'unmuted'} {shown}",
            "level": f"set {shown} to {payload.get('value')} dB", "name": f"named {strip} {payload.get('value')}",
            "colour": f"coloured {shown} {payload.get('value')}", "scene": f"recalled scene {payload.get('value')}"}
    audit.note(request, f"audio_mixers.{action}", f"{d.cfg['name']}: {what[action]}")
    return {"ok": True}


@router.post("/api/admin/audio_mixers/{did}/baseline", dependencies=MANAGE)
async def admin_baseline(request: Request, did: str):
    if service.desk(did) is None:
        return _bad("No such desk.", 404)
    b = await asyncio.to_thread(service.save_baseline, did)
    if b is None:
        return _bad("Connect the desk before saving what it should look like.", 409)
    audit.note(request, "audio_mixers.baseline", f"Saved the watchdog baseline for {service.desk(did).cfg['name']}")
    return _state(request)


@router.get("/api/admin/audio_mixers/people", dependencies=MANAGE)
async def admin_people():
    from ... import users

    rows = await asyncio.to_thread(users.list_users)
    return {"people": [{"id": u["id"], "name": u.get("name") or u.get("username") or f"#{u['id']}"}
                       for u in rows if not u.get("disabled")]}


@router.get("/api/admin/audio_mixers/names", dependencies=CONTROL)
async def admin_names_preview():
    return await asyncio.to_thread(service.name_preview)


@router.post("/api/admin/audio_mixers/names", dependencies=CONTROL)
async def admin_names_push(request: Request, payload: dict):
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return _bad("Tick the names to send.")
    if not payload.get("confirm"):
        return _bad("Confirm before sending names to the desk.")
    got = service.push_names(rows[:256])
    audit.note(request, "audio_mixers.names", f"Sent {got['sent']} mic name{'s' if got['sent'] != 1 else ''} from "
               "Planning Center to the desks")
    return got
