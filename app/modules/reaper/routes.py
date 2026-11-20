"""REAPER admin routes (#413). Mounted by the module registry, so each answers 404 while the module is off. A command
is sent once and never retried: UDP has no answer to wait for, and REAPER's own feedback shows what happened."""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require

from .service import KEY, MAX_MACHINES, Machine, clean_marker, clean_settings, service

router = APIRouter()
VIEW = [Depends(require("reaper.view"))]
CONTROL = [Depends(require("reaper.control"))]
MANAGE = [Depends(require("reaper.manage"))]
TEST_WAIT_S = 2.0


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


async def test_machine(m: Machine) -> dict:
    """Ask the running computer for its whole state and wait for an answer."""
    name = m.cfg["name"]
    if m.error:
        return {"ok": False, "detail": f"{name}: {m.error}"}
    before = m.link.state.heard_at
    if not m.link.refresh():
        return {"ok": False, "detail": f"{name}: Relay could not send to REAPER."}
    end = time.time() + TEST_WAIT_S
    while time.time() < end:
        await asyncio.sleep(0.1)
        if m.link.state.heard_at > before:
            tracks = m.tracks()
            armed = sum(1 for t in tracks if t["armed"])
            return {"ok": True, "detail": f"{name}: REAPER answered: {len(tracks)} track"
                                          f"{'s' if len(tracks) != 1 else ''}, {armed} armed, "
                                          f"{'recording' if m.link.state.recording else 'not recording'}."}
    return {"ok": False, "detail": f"{name}: REAPER did not answer. In REAPER's OSC surface, set the device IP to "
                                   f"this computer and the device port to {m.cfg['listen_port']}."}


@router.get("/api/admin/reaper", dependencies=VIEW)
async def admin_state():
    return service.snapshot()


@router.put("/api/admin/reaper/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    sent = payload.get("machines")
    if not isinstance(sent, list):
        return _bad("Send the list of REAPER computers.")
    if len(sent) > MAX_MACHINES:
        return _bad(f"Relay follows up to {MAX_MACHINES} REAPER computers.")
    saved = {m["id"] for m in service.settings()["machines"]}
    machines = []
    for m in sent:
        if not isinstance(m, dict):
            continue
        if not (isinstance(m.get("id"), str) and m["id"] in saved):
            m = {**m, "id": uuid.uuid4().hex[:8]}
        host = m.get("host")
        if isinstance(host, str) and host.strip() and \
                not clean_settings({"machines": [{"host": host}]})["machines"][0]["host"]:
            return _bad("That is not an address: use the REAPER computer's IP address, like 192.168.1.70, or "
                        "127.0.0.1 for this computer.")
        machines.append(m)
    ports = [m.get("listen_port") for m in machines if isinstance(m.get("listen_port"), int)]
    if len(ports) != len(set(ports)):
        return _bad("Give each REAPER computer its own listen port.")
    new = clean_settings({"machines": machines})
    config.save({KEY: new})
    await service.restart()
    audit.note(request, "reaper.settings", "Changed the REAPER computers",
               target=", ".join(m["name"] for m in new["machines"]) or "(none)")
    return service.snapshot()


@router.post("/api/admin/reaper/test", dependencies=MANAGE)
async def admin_test(payload: dict):
    m = service.machine(payload.get("machine"))
    if m is None:
        return {"ok": False, "detail": "Save this REAPER computer first."}
    return await test_machine(m)


def _which(payload: dict) -> tuple[str | None, JSONResponse | None]:
    mid = payload.get("machine")
    if mid in (None, ""):
        return None, None
    if service.machine(mid) is None:
        return None, _bad("There is no such REAPER computer.", 404)
    return mid, None


@router.post("/api/admin/reaper/record", dependencies=CONTROL)
async def admin_record(request: Request, payload: dict):
    mid, err = _which(payload)
    if err:
        return err
    done = service.record(mid)
    if not done:
        return _bad("No REAPER computer answered.", 502)
    audit.note(request, "reaper.record", f"REAPER ({', '.join(done)}): started recording")
    return {"ok": True, "done": done}


@router.post("/api/admin/reaper/stop", dependencies=CONTROL)
async def admin_stop(request: Request, payload: dict):
    mid, err = _which(payload)
    if err:
        return err
    done = service.stop_recording(mid)
    if not done:
        return _bad("No REAPER computer answered.", 502)
    audit.note(request, "reaper.stop", f"REAPER ({', '.join(done)}): stopped recording")
    return {"ok": True, "done": done}


@router.post("/api/admin/reaper/marker", dependencies=CONTROL)
async def admin_marker(request: Request, payload: dict):
    mid, err = _which(payload)
    if err:
        return err
    name = clean_marker(payload.get("name"))
    done = await service.marker(name, mid)
    if not done:
        return _bad("No REAPER computer answered.", 502)
    audit.note(request, "reaper.marker", f"REAPER ({', '.join(done)}): marker {name}".rstrip())
    return {"ok": True, "done": done}


@router.post("/api/admin/reaper/action", dependencies=CONTROL)
async def admin_action(request: Request, payload: dict):
    mid, err = _which(payload)
    if err:
        return err
    a = payload.get("action")
    if isinstance(a, str) and a.strip().isdigit():
        a = int(a.strip())
    if isinstance(a, bool) or not isinstance(a, int) or not 1 <= a <= 99_999_999:
        return _bad("Give REAPER's action number (its Command ID in the Actions list), like 40157.")
    done = service.action(a, mid)
    if not done:
        return _bad("No REAPER computer answered.", 502)
    audit.note(request, "reaper.action", f"REAPER ({', '.join(done)}): ran action {a}")
    return {"ok": True, "done": done}


@router.post("/api/admin/reaper/track", dependencies=CONTROL)
async def admin_track(request: Request, payload: dict):
    m = service.machine(payload.get("machine"))
    if m is None:
        return _bad("There is no such REAPER computer.", 404)
    n = payload.get("track")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= m.cfg["tracks"]:
        return _bad("Choose a track.")
    t = m.link.state.tracks.get(n)
    label = (t.name if t and t.name else f"Track {n}")[:60]
    sent = []
    if isinstance(payload.get("arm"), bool):
        if not m.link.arm(n, payload["arm"]):
            return _bad("REAPER could not be reached.", 502)
        sent.append(("armed " if payload["arm"] else "disarmed ") + label)
    if isinstance(payload.get("mute"), bool):
        if not m.link.mute(n, payload["mute"]):
            return _bad("REAPER could not be reached.", 502)
        sent.append(("muted " if payload["mute"] else "unmuted ") + label)
    if not sent:
        return _bad("Say whether to arm or mute the track.")
    audit.note(request, "reaper.track", f"REAPER ({m.cfg['name']}): {', '.join(sent)}", target=m.id)
    return {"ok": True}
