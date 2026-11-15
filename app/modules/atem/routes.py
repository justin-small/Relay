"""ATEM admin routes (#243). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from .service import KEY, clean_settings, service

router = APIRouter()
VIEW = [Depends(require("atem.view"))]
CONTROL = [Depends(require("atem.control"))]
MANAGE = [Depends(require("atem.manage"))]


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


def _int(value, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _done(ok: bool):
    return {"ok": True} if ok else _bad("The switcher is not connected.", 409)


@router.get("/api/admin/atem", dependencies=VIEW)
def admin_state():
    return service.snapshot()


@router.put("/api/admin/atem/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    host = payload.get("host", "")
    if not isinstance(host, str):
        return _bad("Enter the switcher's address.")
    s = clean_settings({"host": host, "port": payload.get("port", 9910)})
    if host.strip() and not s["host"]:
        return _bad("That is not an address: use the switcher's IP address, like 192.168.1.240.")
    config.save({KEY: s})
    await service.restart()
    audit.note(request, "atem.settings", "Changed the ATEM's address", target=s["host"] or "(none)")
    return service.snapshot()


def _me(payload: dict) -> int | None:
    return _int(payload.get("me", 0), 0, max(0, service.limits()["mix_effects"] - 1))


@router.post("/api/admin/atem/cut", dependencies=CONTROL)
def admin_cut(request: Request, payload: dict):
    me = _me(payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.cut(me)
    if ok:
        audit.note(request, "atem.cut", "Cut on the ATEM", target=f"M/E {me + 1}")
    return _done(ok)


@router.post("/api/admin/atem/auto", dependencies=CONTROL)
def admin_auto(request: Request, payload: dict):
    me = _me(payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.auto(me)
    if ok:
        audit.note(request, "atem.auto", "Took an auto transition on the ATEM", target=f"M/E {me + 1}")
    return _done(ok)


@router.post("/api/admin/atem/fade-to-black", dependencies=CONTROL)
def admin_ftb(request: Request, payload: dict):
    me = _me(payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.fade_to_black(me)
    if ok:
        audit.note(request, "atem.fade_to_black", "Fade to black on the ATEM", target=f"M/E {me + 1}")
    return _done(ok)


@router.post("/api/admin/atem/program", dependencies=CONTROL)
def admin_program(request: Request, payload: dict):
    me, source = _me(payload), _int(payload.get("source"), 0, 65535)
    if me is None or source is None:
        return _bad("Choose a mix effect and an input.")
    ok = service.set_program(me, source)
    if ok:
        audit.note(request, "atem.program", "Put an input on program", target=f"M/E {me + 1}",
                   detail={"source": source})
    return _done(ok)


@router.post("/api/admin/atem/preview", dependencies=CONTROL)
def admin_preview(request: Request, payload: dict):
    me, source = _me(payload), _int(payload.get("source"), 0, 65535)
    if me is None or source is None:
        return _bad("Choose a mix effect and an input.")
    ok = service.set_preview(me, source)
    if ok:
        audit.note(request, "atem.preview", "Put an input on preview", target=f"M/E {me + 1}",
                   detail={"source": source})
    return _done(ok)


@router.post("/api/admin/atem/downstream-key", dependencies=CONTROL)
def admin_dsk(request: Request, payload: dict):
    keyer = _int(payload.get("keyer", 0), 0, max(0, service.limits()["keyers"] - 1))
    action = payload.get("action")
    if keyer is None or action not in ("auto", "on", "off") or service.limits()["keyers"] == 0:
        return _bad("Choose a downstream key and auto, on or off.")
    ok = service.downstream(keyer, action)
    if ok:
        audit.note(request, "atem.downstream_key", "Changed a downstream key on the ATEM",
                   target=f"Key {keyer + 1}", detail={"action": action})
    return _done(ok)


@router.post("/api/admin/atem/macro", dependencies=CONTROL)
def admin_macro(request: Request, payload: dict):
    action = payload.get("action", "run")
    slot = _int(payload.get("slot", 0), 0, 99)
    if action not in ("run", "stop") or slot is None:
        return _bad("Choose a macro and run or stop.")
    ok = service.macro(slot, action)
    if ok:
        audit.note(request, "atem.macro", "Ran or stopped an ATEM macro", detail={"slot": slot, "action": action})
    return _done(ok)
