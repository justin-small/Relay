"""ATEM admin routes (#243, #386). Mounted by the module registry, so each answers 404 while the module is off.

Control routes take `"device": "<id>"` in the body to say which switcher; without it they act on the first one, so a
page or tool written for one switcher keeps working.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit
from .. import require
from . import discovery
from .service import (MAX_DEVICES, NAME_MAX, clean_device, clean_host, clean_offset, devices, new_id, save_devices,
                      service)

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


# ---------------------------------------------------------------------------------------------- the switcher list
def _next_offset(items: list[dict]) -> int:
    """The first camera number for a new switcher: 0 for the first, else the next free hundred."""
    used = {d["tally_offset"] for d in items if d["tally"]}
    return next((n for n in range(0, 901, 100) if n not in used), 0)


def _apply(item: dict, payload: dict) -> str:
    """Copy the editable fields from a request onto a switcher. A sentence when one is not usable."""
    if "host" in payload:
        host = payload.get("host")
        if not isinstance(host, str) or (host.strip() and not clean_host(host)):
            return "That is not an address: use the switcher's IP address, like 192.168.1.240."
        item["host"] = clean_host(host)
    if "port" in payload:
        if _int(payload.get("port"), 1, 65535) is None:
            return "The port is a number from 1 to 65535 (an ATEM uses 9910)."
        item["port"] = payload["port"]
    if "name" in payload:
        if not isinstance(payload.get("name"), str) or not payload["name"].strip():
            return "Give the switcher a name."
        item["name"] = payload["name"]
    for flag in ("enabled", "tally"):
        if flag in payload:
            if not isinstance(payload.get(flag), bool):
                return f"'{flag}' is true or false."
            item[flag] = payload[flag]
    if "tally_offset" in payload:
        if _int(payload.get("tally_offset"), 0, 900) is None:
            return "The first camera number is from 1 to 901."
        item["tally_offset"] = clean_offset(payload["tally_offset"])
    for text in ("uid", "model"):
        if isinstance(payload.get(text), str):
            item[text] = payload[text]
    return ""


@router.post("/api/admin/atem/devices", dependencies=MANAGE)
async def admin_add(request: Request, payload: dict):
    items = devices()
    if len(items) >= MAX_DEVICES:
        return _bad(f"Relay follows at most {MAX_DEVICES} switchers.")
    host = clean_host(payload.get("host"))
    if host and any(d["host"] == host and d["port"] == payload.get("port", 9910) for d in items):
        return _bad("That switcher is already in the list.", 409)
    item = {"id": new_id(), "name": f"Switcher {len(items) + 1}", "host": "", "port": 9910, "enabled": True,
            "tally": True, "tally_offset": _next_offset(items)}
    problem = _apply(item, payload)
    if problem:
        return _bad(problem)
    if not item["host"]:
        return _bad("Enter the switcher's address.")
    item = clean_device(item)
    save_devices(items + [item])
    await service.sync()
    audit.note(request, "atem.device_add", "Added an ATEM switcher", target=item["name"], detail={"host": item["host"]})
    return service.snapshot()


@router.put("/api/admin/atem/devices/{device_id}", dependencies=MANAGE)
async def admin_edit(request: Request, device_id: str, payload: dict):
    items = devices()
    item = next((d for d in items if d["id"] == device_id), None)
    if item is None:
        return _bad("That switcher is not in the list.", 404)
    problem = _apply(item, payload)
    if problem:
        return _bad(problem)
    if not item["host"]:
        return _bad("Enter the switcher's address.")
    items = [clean_device(d) if d is item else d for d in items]
    save_devices(items)
    await service.sync()
    audit.note(request, "atem.device_edit", "Changed an ATEM switcher", target=item["name"][:NAME_MAX],
               detail={k: payload[k] for k in ("host", "port", "enabled", "tally", "tally_offset") if k in payload})
    return service.snapshot()


@router.delete("/api/admin/atem/devices/{device_id}", dependencies=MANAGE)
async def admin_remove(request: Request, device_id: str):
    items = devices()
    item = next((d for d in items if d["id"] == device_id), None)
    if item is None:
        return _bad("That switcher is not in the list.", 404)
    save_devices([d for d in items if d["id"] != device_id])
    await service.sync()
    audit.note(request, "atem.device_remove", "Removed an ATEM switcher", target=item["name"])
    return service.snapshot()


@router.post("/api/admin/atem/scan", dependencies=MANAGE)
def admin_scan(request: Request, payload: dict | None = None):
    """Look for Blackmagic gear: mDNS always, and the subnet probe when asked (`{"probe": true}`)."""
    payload = payload or {}
    found = discovery.discover()
    probed = payload.get("probe") is True
    if probed:
        have = {f["address"] for f in found if f["address"]}
        found += [f for f in discovery.probe() if f["address"] not in have]
    items = devices()
    for f in found:
        f["added"] = next((d["id"] for d in items if (f["uid"] and d["uid"] == f["uid"])
                           or (f["address"] and d["host"] == f["address"])), None)
    audit.note(request, "atem.scan", "Looked for Blackmagic devices on the network",
               detail={"found": len(found), "probe": probed})
    return {"found": found, "probed": probed}


# ---------------------------------------------------------------------------------------------- control
def _target(payload: dict):
    """The switcher a control request is for, or an error response."""
    dev = service.device(payload.get("device"))
    if dev is None:
        return None, _bad("That switcher is not in the list.", 404)
    return dev, None


def _me(dev, payload: dict) -> int | None:
    return _int(payload.get("me", 0), 0, max(0, service.limits(dev)["mix_effects"] - 1))


def _where(dev, me: int) -> str:
    return f"{dev.cfg['name']} M/E {me + 1}"


@router.post("/api/admin/atem/cut", dependencies=CONTROL)
def admin_cut(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me = _me(dev, payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.cut(dev, me)
    if ok:
        audit.note(request, "atem.cut", "Cut on the ATEM", target=_where(dev, me))
    return _done(ok)


@router.post("/api/admin/atem/auto", dependencies=CONTROL)
def admin_auto(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me = _me(dev, payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.auto(dev, me)
    if ok:
        audit.note(request, "atem.auto", "Took an auto transition on the ATEM", target=_where(dev, me))
    return _done(ok)


@router.post("/api/admin/atem/fade-to-black", dependencies=CONTROL)
def admin_ftb(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me = _me(dev, payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.fade_to_black(dev, me)
    if ok:
        audit.note(request, "atem.fade_to_black", "Fade to black on the ATEM", target=_where(dev, me))
    return _done(ok)


@router.post("/api/admin/atem/program", dependencies=CONTROL)
def admin_program(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me, source = _me(dev, payload), _int(payload.get("source"), 0, 65535)
    if me is None or source is None:
        return _bad("Choose a mix effect and an input.")
    ok = service.set_program(dev, me, source)
    if ok:
        audit.note(request, "atem.program", "Put an input on program", target=_where(dev, me),
                   detail={"source": source})
    return _done(ok)


@router.post("/api/admin/atem/preview", dependencies=CONTROL)
def admin_preview(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me, source = _me(dev, payload), _int(payload.get("source"), 0, 65535)
    if me is None or source is None:
        return _bad("Choose a mix effect and an input.")
    ok = service.set_preview(dev, me, source)
    if ok:
        audit.note(request, "atem.preview", "Put an input on preview", target=_where(dev, me),
                   detail={"source": source})
    return _done(ok)


@router.post("/api/admin/atem/downstream-key", dependencies=CONTROL)
def admin_dsk(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    keyers = service.limits(dev)["keyers"]
    keyer = _int(payload.get("keyer", 0), 0, max(0, keyers - 1))
    action = payload.get("action")
    if keyer is None or action not in ("auto", "on", "off") or keyers == 0:
        return _bad("Choose a downstream key and auto, on or off.")
    ok = service.downstream(dev, keyer, action)
    if ok:
        audit.note(request, "atem.downstream_key", "Changed a downstream key on the ATEM",
                   target=f"{dev.cfg['name']} key {keyer + 1}", detail={"action": action})
    return _done(ok)


@router.post("/api/admin/atem/macro", dependencies=CONTROL)
def admin_macro(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    action = payload.get("action", "run")
    slot = _int(payload.get("slot", 0), 0, 99)
    if action not in ("run", "stop") or slot is None:
        return _bad("Choose a macro and run or stop.")
    ok = service.macro(dev, slot, action)
    if ok:
        audit.note(request, "atem.macro", "Ran or stopped an ATEM macro", target=dev.cfg["name"],
                   detail={"slot": slot, "action": action})
    return _done(ok)
