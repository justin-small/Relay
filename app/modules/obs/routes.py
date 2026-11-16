"""OBS admin routes and the scene screen (#250). Mounted by the module registry, so each answers 404 while the module is
off."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ... import audit, config
from .. import require
from .client import Connection, ObsError
from .service import KEY, MAX_MACHINES, _passwords, clean_settings, new_key, service

router = APIRouter()
VIEW = [Depends(require("obs.view"))]
CONTROL = [Depends(require("obs.control"))]
MANAGE = [Depends(require("obs.manage"))]


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


async def test_machine(cfg: dict, password: str) -> dict:
    """Connect once, apart from the held connection, and say what OBS reports."""
    name = cfg.get("name") or "OBS"
    try:
        async with Connection.open(cfg["host"], cfg["port"], password) as conn:
            v = await conn.request("GetVersion")
            scenes = await conn.request("GetSceneList")
    except ObsError as e:
        return {"ok": False, "detail": f"{name}: {e}"}
    except Exception:
        return {"ok": False, "detail": f"{name}: OBS did not answer."}
    n = len(scenes.get("scenes") or [])
    return {"ok": True, "detail": f"{name}: connected to OBS {str(v.get('obsVersion', ''))[:20]} "
                                  f"(obs-websocket {str(v.get('obsWebSocketVersion', ''))[:20]}), {n} scene{'s' if n != 1 else ''}."}


@router.get("/api/admin/obs", dependencies=VIEW)
async def admin_state():
    return service.snapshot()


@router.put("/api/admin/obs/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    """Replace the list of OBS computers. A blank password keeps the saved one; `clear_password` removes it. The
    scene-screen key and its switch are changed only by the screen route."""
    sent = payload.get("machines")
    if not isinstance(sent, list):
        return _bad("Send the list of OBS computers.")
    if len(sent) > MAX_MACHINES:
        return _bad(f"Relay follows up to {MAX_MACHINES} OBS computers.")
    cur = service.settings()
    saved = {m["id"]: m for m in cur["machines"]}
    pw = _passwords(cur["passwords"])
    machines = []
    for m in sent:
        if not isinstance(m, dict):
            continue
        if not (isinstance(m.get("id"), str) and m["id"] in saved):
            m = {**m, "id": uuid.uuid4().hex[:8]}     # a new computer: its id now, so its password follows it
        host = m.get("host")
        if isinstance(host, str) and host.strip() and not clean_settings({"machines": [{"host": host}]})["machines"][0]["host"]:
            return _bad("That is not an address: use the OBS computer's IP address, like 192.168.1.60.")
        old = saved.get(m.get("id")) or {}
        machines.append({**m, "key": old.get("key", ""), "screen_on": old.get("screen_on", False)})
    new = clean_settings({"machines": machines})
    by_id = {m["id"]: m for m in machines}
    keep: dict[str, str] = {}
    for m in new["machines"]:
        s = by_id.get(m["id"]) or {}
        if isinstance(s.get("password"), str) and s["password"]:
            keep[m["id"]] = s["password"][:200]
        elif s.get("clear_password") is not True and m["id"] in pw:
            keep[m["id"]] = pw[m["id"]]
    new = clean_settings({"machines": new["machines"], "passwords": keep})
    config.save({KEY: new})
    await service.restart()
    audit.note(request, "obs.settings", "Changed the OBS computers",
               target=", ".join(m["name"] for m in new["machines"]) or "(none)")
    return service.snapshot()


@router.post("/api/admin/obs/test", dependencies=MANAGE)
async def admin_test(payload: dict):
    m = service.machine(payload.get("machine"))
    if m is None:
        return {"ok": False, "detail": "Save this OBS computer first."}
    return await test_machine(m.cfg, m.password)


@router.post("/api/admin/obs/scene", dependencies=CONTROL)
async def admin_scene(request: Request, payload: dict):
    m = service.machine(payload.get("machine"))
    scene = payload.get("scene")
    if m is None:
        return _bad("There is no such OBS computer.", 404)
    if not isinstance(scene, str):
        return _bad("Choose a scene.")
    try:
        await m.set_scene(scene)
    except ObsError as e:
        return _bad(str(e), 502)
    audit.note(request, "obs.scene", f"OBS ({m.cfg['name']}): switched to {scene[:60]}", target=m.id)
    return {"ok": True}


def _machine_or_404(payload: dict):
    m = service.machine(payload.get("machine"))
    return m, (None if m is not None else _bad("There is no such OBS computer.", 404))


@router.post("/api/admin/obs/output", dependencies=CONTROL)
async def admin_output(request: Request, payload: dict):
    """Start or stop the stream, start, stop, pause or resume the recording, or start, stop or save the replay
    buffer. Sent once and never retried: a dropped connection may already have done it."""
    m, err = _machine_or_404(payload)
    if err:
        return err
    which, action = payload.get("output"), payload.get("action")
    if not isinstance(which, str) or not isinstance(action, str):
        return _bad("Choose an output and what to do.")
    try:
        await m.output(which, action)
    except ObsError as e:
        return _bad(str(e), 502)
    words = {"stream": "the stream", "record": "the recording", "replay": "the replay buffer"}
    did = {"start": "Started", "stop": "Stopped", "pause": "Paused", "resume": "Resumed", "save": "Saved"}[action]
    audit.note(request, f"obs.{which}", f"OBS ({m.cfg['name']}): {did} {words[which]}", target=m.id)
    return {"ok": True}


@router.post("/api/admin/obs/studio", dependencies=CONTROL)
async def admin_studio(request: Request, payload: dict):
    """Studio mode: switch it on or off, put a scene in preview, or take the preview to program."""
    m, err = _machine_or_404(payload)
    if err:
        return err
    action, scene = payload.get("action"), payload.get("scene")
    try:
        if action in ("on", "off"):
            await m.set_studio(action == "on")
            note = f"Turned studio mode {action}"
        elif action == "preview" and isinstance(scene, str):
            await m.set_preview(scene)
            note = f"Put {scene[:60]} in preview"
        elif action == "take":
            await m.take()
            note = f"Took {m.preview_scene[:60] or 'the preview'} to program"
        else:
            return _bad("Choose on, off, preview or take.")
    except ObsError as e:
        return _bad(str(e), 502)
    audit.note(request, "obs.studio", f"OBS ({m.cfg['name']}): {note}", target=m.id)
    return {"ok": True}


@router.post("/api/admin/obs/mute", dependencies=CONTROL)
async def admin_mute(request: Request, payload: dict):
    m, err = _machine_or_404(payload)
    if err:
        return err
    name, muted = payload.get("input"), payload.get("muted")
    if not isinstance(name, str) or not isinstance(muted, bool):
        return _bad("Choose an input and whether it is muted.")
    try:
        await m.set_mute(name, muted)
    except ObsError as e:
        return _bad(str(e), 502)
    audit.note(request, "obs.mute", f"OBS ({m.cfg['name']}): {'Muted' if muted else 'Unmuted'} {name[:60]}", target=m.id)
    return {"ok": True}


@router.get("/api/admin/obs/audio/{machine_id}", dependencies=VIEW)
async def admin_audio(machine_id: str):
    """The watched inputs' meters and mutes, read a few times a second by the admin page."""
    m = service.machine(machine_id)
    if m is None:
        return _bad("There is no such OBS computer.", 404)
    return JSONResponse({"audio": m.audio()}, headers={"Cache-Control": "no-store"})


@router.get("/api/admin/obs/preview/{machine_id}", dependencies=VIEW)
async def admin_preview(machine_id: str):
    """The program scene as a small JPEG, while that computer's preview is switched on. Never on the viewer port's
    pages: only this signed-in route serves it."""
    m = service.machine(machine_id)
    if m is None or not m.cfg["preview"]:
        return _bad("The preview is off.", 404)
    try:
        img = await m.screenshot()
    except ObsError as e:
        return _bad(str(e), 502)
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _link(request: Request, mid: str, key: str) -> str:
    return f"{request.url.scheme}://{request.url.netloc}/screen?obs={mid}&key={key}"


@router.put("/api/admin/obs/screen/{machine_id}", dependencies=MANAGE)
async def admin_screen(machine_id: str, request: Request, payload: dict):
    """Switch a computer's scene screen on or off, or rotate its key (the old link stops at once). Answers with the
    link: the only place the key is ever sent."""
    cur = service.settings()
    m = next((x for x in cur["machines"] if x["id"] == machine_id), None)
    if m is None:
        return _bad("There is no such OBS computer.", 404)
    on = payload.get("on") is True
    rotate = payload.get("rotate") is True
    if on and (not m["key"] or rotate):
        m["key"] = new_key()
    elif rotate:
        return _bad("Turn the screen on first.")
    m["screen_on"] = on
    config.save({KEY: cur})
    live = service.machine(machine_id)
    if live is not None:
        live.cfg.update(key=m["key"], screen_on=on)
    audit.note(request, "obs.screen", f"{'Turned on' if on else 'Turned off'} the OBS scene screen for {m['name']}"
               + (" with a new key" if rotate else ""), target=machine_id)
    return {"on": on, "link": _link(request, machine_id, m["key"]) if on else ""}


@router.get("/api/admin/obs/screen/{machine_id}", dependencies=MANAGE)
async def admin_screen_link(machine_id: str, request: Request):
    m = next((x for x in service.settings()["machines"] if x["id"] == machine_id), None)
    if m is None:
        return _bad("There is no such OBS computer.", 404)
    return {"on": m["screen_on"], "link": _link(request, machine_id, m["key"]) if m["screen_on"] else ""}


# ====================================================================================================== /screen
# A computer's scenes on a browser screen: plain HTTP with no sign-in, so a tablet in the booth needs nothing but its
# link. The link's per-computer key is its whole authority: it can switch that computer's scenes and nothing else.
WINDOW_S = 10.0
MAX_PRESSES = 20          # per client address per window
MAX_BAD_KEYS = 8          # wrong keys per client address per window
_hits: dict[str, list[float]] = {}
_bad_hits: dict[str, list[float]] = {}


def _limited(table: dict, who: str, limit: int) -> bool:
    now = time.monotonic()
    if len(table) > 2000:
        for k in [k for k, v in table.items() if not v or now - v[-1] > WINDOW_S]:
            table.pop(k, None)
    recent = [t for t in table.get(who, []) if now - t < WINDOW_S]
    if len(recent) >= limit:
        table[who] = recent
        return True
    table[who] = recent + [now]
    return False


def _who(request: Request) -> str:
    return request.client.host if request.client else ""


def _locked(request: Request) -> bool:
    hits = _bad_hits.get(_who(request), [])
    return len(hits) >= MAX_BAD_KEYS and time.monotonic() - hits[-1] < WINDOW_S


def _bad_key(request: Request) -> None:
    who = _who(request)
    now = time.monotonic()
    _bad_hits[who] = [t for t in _bad_hits.get(who, []) if now - t < WINDOW_S][-MAX_BAD_KEYS:] + [now]


def _checked(request: Request, mid, key):
    """(machine, None) when the key is right, else (None, an answer). Wrong keys are counted, never logged."""
    if _locked(request):
        return None, JSONResponse({"error": "slow"}, status_code=429)
    m = service.screen_machine(mid, key) if isinstance(mid, str) and isinstance(key, str) else None
    if m is None:
        _bad_key(request)
        return None, JSONResponse({"error": "refused"}, status_code=404)
    return m, None


async def screen_page(request: Request):
    """/screen?obs=<computer>&key=<key>: that computer's scenes as big buttons. The same plain 404 whether the
    computer does not exist, its screen is off or the key is wrong."""
    from .. import templates

    m, err = _checked(request, request.query_params.get("obs", ""), request.query_params.get("key", ""))
    if m is None:
        return err if err.status_code == 429 else HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse(request, "obs/screen.html", {
        "machine": {"id": m.id, "name": m.cfg["name"]}, "key": m.cfg["key"]},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/obs/_/state/{machine_id}")
async def screen_state(machine_id: str, request: Request, key: str = ""):
    m, err = _checked(request, machine_id, key)
    if m is None:
        return err
    return JSONResponse({"connected": m.state == "connected", "scenes": m.scenes, "program": m.program,
                         "streaming": bool(m.stream.get("outputActive")),
                         "recording": bool(m.record.get("outputActive"))},
                        headers={"Cache-Control": "no-store"})


@router.post("/obs/_/scene/{machine_id}")
async def screen_scene(machine_id: str, request: Request, payload: dict, key: str = ""):
    """One switch from a screen. Answers {"ok": true} or {"error": "unreachable" | "refused" | "slow"}: a short code,
    never OBS's own words. Logged only after the key checked out."""
    m, err = _checked(request, machine_id, key)
    if m is None:
        return err
    scene = payload.get("scene")
    if not isinstance(scene, str) or scene not in m.scenes:
        return JSONResponse({"error": "refused"}, status_code=404)
    if _limited(_hits, _who(request), MAX_PRESSES):
        return JSONResponse({"error": "slow"}, status_code=429)
    try:
        await m.set_scene(scene)
    except ObsError:
        return JSONResponse({"error": "unreachable"}, status_code=502)
    audit.record("obs.scene", f"OBS ({m.cfg['name']}): switched to {scene[:60]} (from the scene screen)", target=m.id)
    return {"ok": True}
