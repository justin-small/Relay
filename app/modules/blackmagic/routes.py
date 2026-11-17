"""Blackmagic admin routes (#243, #386, #432, #433). Mounted by the module registry, so each answers 404 while the
module is off.

Control routes take `"device": "<id>"` in the body to say which device; the ATEM routes without it act on the first
switcher, so a page or tool written for one switcher keeps working. Every device has a `kind`: `atem`, `hyperdeck` or
`videohub`, and a control route refuses a device of another kind.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit
from .. import require
from . import discovery, videohub
from .service import (DEFAULT_NAME, DEFAULT_PORT as DEFAULT_PORTS, KIND_NAMES, KINDS, MAX_DEVICES, MAX_PRESETS, NAME_MAX, clean_device, clean_host,
                      clean_offset, devices, new_id, save_devices, service)

router = APIRouter()
VIEW = [Depends(require("blackmagic.view"))]
CONTROL = [Depends(require("blackmagic.control"))]
MANAGE = [Depends(require("blackmagic.manage"))]


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


def _int(value, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _done(ok: bool, what: str = "The switcher"):
    return {"ok": True} if ok else _bad(f"{what} is not connected.", 409)


@router.get("/api/admin/blackmagic", dependencies=VIEW)
def admin_state():
    return service.snapshot()


# ---------------------------------------------------------------------------------------------- the device list
def _next_offset(items: list[dict]) -> int:
    """The first camera number for a new switcher: 0 for the first, else the next free hundred."""
    used = {d["tally_offset"] for d in items if d["kind"] == "atem" and d["tally"]}
    return next((n for n in range(0, 901, 100) if n not in used), 0)


def _apply(item: dict, payload: dict) -> str:
    """Copy the editable fields from a request onto a device. A sentence when one is not usable."""
    if "host" in payload:
        host = payload.get("host")
        if not isinstance(host, str) or (host.strip() and not clean_host(host)):
            return "That is not an address: use the device's IP address, like 192.168.1.240."
        item["host"] = clean_host(host)
    if "port" in payload:
        if _int(payload.get("port"), 1, 65535) is None:
            return "The port is a number from 1 to 65535."
        item["port"] = payload["port"]
    if "name" in payload:
        if not isinstance(payload.get("name"), str) or not payload["name"].strip():
            return "Give the device a name."
        item["name"] = payload["name"]
    if "enabled" in payload:
        if not isinstance(payload.get("enabled"), bool):
            return "'enabled' is true or false."
        item["enabled"] = payload["enabled"]
    if item["kind"] == "atem":
        if "tally" in payload:
            if not isinstance(payload.get("tally"), bool):
                return "'tally' is true or false."
            item["tally"] = payload["tally"]
        if "tally_offset" in payload:
            if _int(payload.get("tally_offset"), 0, 900) is None:
                return "The first camera number is from 1 to 901."
            item["tally_offset"] = clean_offset(payload["tally_offset"])
    for text in ("uid", "model"):
        if isinstance(payload.get(text), str):
            item[text] = payload[text]
    return ""


@router.post("/api/admin/blackmagic/devices", dependencies=MANAGE)
async def admin_add(request: Request, payload: dict):
    items = devices()
    if len(items) >= MAX_DEVICES:
        return _bad(f"Relay follows at most {MAX_DEVICES} Blackmagic devices.")
    kind = payload.get("kind", "atem")
    if kind not in KINDS:
        return _bad("Choose ATEM, HyperDeck or Videohub.")
    port = DEFAULT_PORTS[kind]
    host = clean_host(payload.get("host"))
    if host and any(d["host"] == host and d["kind"] == kind and d["port"] == payload.get("port", port) for d in items):
        return _bad("That device is already in the list.", 409)
    same = sum(1 for d in items if d["kind"] == kind)
    item = {"id": new_id(), "kind": kind, "name": f"{DEFAULT_NAME[kind]} {same + 1}", "host": "", "port": port,
            "enabled": True}
    if kind == "atem":
        item.update(tally=True, tally_offset=_next_offset(items))
    problem = _apply(item, payload)
    if problem:
        return _bad(problem)
    if not item["host"]:
        return _bad("Enter the device's address.")
    item = clean_device(item)
    save_devices(items + [item])
    await service.sync()
    audit.note(request, "blackmagic.device_add", f"Added a Blackmagic {KIND_NAMES[kind]}", target=item["name"],
               detail={"host": item["host"], "kind": kind})
    return service.snapshot()


@router.put("/api/admin/blackmagic/devices/{device_id}", dependencies=MANAGE)
async def admin_edit(request: Request, device_id: str, payload: dict):
    items = devices()
    item = next((d for d in items if d["id"] == device_id), None)
    if item is None:
        return _bad("That device is not in the list.", 404)
    problem = _apply(item, payload)
    if problem:
        return _bad(problem)
    if not item["host"]:
        return _bad("Enter the device's address.")
    items = [clean_device(d) if d is item else d for d in items]
    save_devices(items)
    await service.sync()
    audit.note(request, "blackmagic.device_edit", f"Changed a Blackmagic {KIND_NAMES[item['kind']]}",
               target=item["name"][:NAME_MAX],
               detail={k: payload[k] for k in ("host", "port", "enabled", "tally", "tally_offset") if k in payload})
    return service.snapshot()


@router.delete("/api/admin/blackmagic/devices/{device_id}", dependencies=MANAGE)
async def admin_remove(request: Request, device_id: str):
    items = devices()
    item = next((d for d in items if d["id"] == device_id), None)
    if item is None:
        return _bad("That device is not in the list.", 404)
    save_devices([d for d in items if d["id"] != device_id])
    await service.sync()
    audit.note(request, "blackmagic.device_remove", f"Removed a Blackmagic {KIND_NAMES[item['kind']]}", target=item["name"])
    return service.snapshot()


@router.post("/api/admin/blackmagic/scan", dependencies=MANAGE)
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
        f["added"] = next((d["id"] for d in items if d["kind"] == f["family"] and (
            (f["uid"] and d["uid"].lower() == f["uid"].lower()) or (f["address"] and d["host"] == f["address"]))), None)
    audit.note(request, "blackmagic.scan", "Looked for Blackmagic devices on the network",
               detail={"found": len(found), "probe": probed})
    return {"found": found, "probed": probed}


# ---------------------------------------------------------------------------------------------- control
def _target(payload: dict, kind: str = "atem"):
    """The device a control request is for, or an error response."""
    dev = service.device(payload.get("device"))
    if dev is None or dev.cfg["kind"] != kind:
        return None, _bad(f"That {'switcher' if kind == 'atem' else KIND_NAMES[kind]} is not in the list.", 404)
    return dev, None


def _me(dev, payload: dict) -> int | None:
    return _int(payload.get("me", 0), 0, max(0, service.limits(dev)["mix_effects"] - 1))


def _where(dev, me: int) -> str:
    return f"{dev.cfg['name']} M/E {me + 1}"


@router.post("/api/admin/blackmagic/cut", dependencies=CONTROL)
def admin_cut(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me = _me(dev, payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.cut(dev, me)
    if ok:
        audit.note(request, "blackmagic.cut", "Cut on the ATEM", target=_where(dev, me))
    return _done(ok)


@router.post("/api/admin/blackmagic/auto", dependencies=CONTROL)
def admin_auto(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me = _me(dev, payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.auto(dev, me)
    if ok:
        audit.note(request, "blackmagic.auto", "Took an auto transition on the ATEM", target=_where(dev, me))
    return _done(ok)


@router.post("/api/admin/blackmagic/fade-to-black", dependencies=CONTROL)
def admin_ftb(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me = _me(dev, payload)
    if me is None:
        return _bad("That mix effect does not exist.")
    ok = service.fade_to_black(dev, me)
    if ok:
        audit.note(request, "blackmagic.fade_to_black", "Fade to black on the ATEM", target=_where(dev, me))
    return _done(ok)


@router.post("/api/admin/blackmagic/program", dependencies=CONTROL)
def admin_program(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me, source = _me(dev, payload), _int(payload.get("source"), 0, 65535)
    if me is None or source is None:
        return _bad("Choose a mix effect and an input.")
    ok = service.set_program(dev, me, source)
    if ok:
        audit.note(request, "blackmagic.program", "Put an input on program", target=_where(dev, me),
                   detail={"source": source})
    return _done(ok)


@router.post("/api/admin/blackmagic/preview", dependencies=CONTROL)
def admin_preview(request: Request, payload: dict):
    dev, err = _target(payload)
    if err:
        return err
    me, source = _me(dev, payload), _int(payload.get("source"), 0, 65535)
    if me is None or source is None:
        return _bad("Choose a mix effect and an input.")
    ok = service.set_preview(dev, me, source)
    if ok:
        audit.note(request, "blackmagic.preview", "Put an input on preview", target=_where(dev, me),
                   detail={"source": source})
    return _done(ok)


@router.post("/api/admin/blackmagic/downstream-key", dependencies=CONTROL)
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
        audit.note(request, "blackmagic.downstream_key", "Changed a downstream key on the ATEM",
                   target=f"{dev.cfg['name']} key {keyer + 1}", detail={"action": action})
    return _done(ok)


@router.post("/api/admin/blackmagic/macro", dependencies=CONTROL)
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
        audit.note(request, "blackmagic.macro", "Ran or stopped an ATEM macro", target=dev.cfg["name"],
                   detail={"slot": slot, "action": action})
    return _done(ok)


# ---------------------------------------------------------------------------------------------- HyperDeck
@router.post("/api/admin/blackmagic/hyperdeck/{action}", dependencies=CONTROL)
def admin_deck(request: Request, action: str, payload: dict):
    if action not in ("record", "stop", "play"):
        return _bad("Choose record, stop or play.", 404)
    dev, err = _target(payload, "hyperdeck")
    if err:
        return err
    ok = service.deck(dev, action)
    if ok:
        audit.note(request, "blackmagic.hyperdeck_" + action, f"HyperDeck: {action}", target=dev.cfg["name"])
    return _done(ok, "The HyperDeck")


# ---------------------------------------------------------------------------------------------- Videohub
def _hub(payload: dict):
    """The connected Videohub a request is for, or an error response."""
    dev, err = _target(payload, "videohub")
    if err:
        return None, None, err
    hub = service.hub(dev)
    if hub is None:
        return None, None, _bad("The Videohub is not connected.", 409)
    return dev, hub, None


def _port(hub, value, kind: str) -> int | None:
    limit = hub.state.outputs if kind == "output" else hub.state.inputs
    return _int(value, 0, max(0, limit - 1)) if limit else None


@router.post("/api/admin/blackmagic/videohub/route", dependencies=CONTROL)
def admin_hub_route(request: Request, payload: dict):
    dev, hub, err = _hub(payload)
    if err:
        return err
    out, src = _port(hub, payload.get("output"), "output"), _port(hub, payload.get("input"), "input")
    if out is None or src is None:
        return _bad("Choose an output and an input that exist.")
    if not hub.can_route(out):
        return _bad("Another control surface has locked that output.", 409)
    sent, _ = service.route(dev, {out: src})
    if not sent:
        return _bad("The Videohub is not connected.", 409)
    audit.note(request, "blackmagic.videohub_route", "Changed a Videohub route", target=dev.cfg["name"],
               detail={"output": out + 1, "input": src + 1})
    return {"ok": True}


@router.post("/api/admin/blackmagic/videohub/labels", dependencies=MANAGE)
def admin_hub_labels(request: Request, payload: dict):
    dev, hub, err = _hub(payload)
    if err:
        return err
    kind = payload.get("kind")
    raw = payload.get("labels")
    if kind not in ("input", "output") or not isinstance(raw, dict) or not raw or len(raw) > videohub.MAX_PORTS:
        return _bad("Send the labels to change as input or output numbers and their new names.")
    labels: dict[int, str] = {}
    for key, text in raw.items():
        n = _port(hub, int(key), kind) if isinstance(key, str) and key.isascii() and key.isdigit() else None
        label = videohub.clean_label(text)
        if n is None or not label:
            return _bad("Each label needs a port that exists and a name.")
        labels[n] = label
    if not hub.set_labels(kind, labels):
        return _bad("The Videohub is not connected.", 409)
    audit.note(request, "blackmagic.videohub_labels", f"Renamed Videohub {kind}s", target=dev.cfg["name"],
               detail={"count": len(labels)})
    return {"ok": True}


@router.post("/api/admin/blackmagic/videohub/lock", dependencies=MANAGE)
def admin_hub_lock(request: Request, payload: dict):
    dev, hub, err = _hub(payload)
    if err:
        return err
    out, action = _port(hub, payload.get("output"), "output"), payload.get("action")
    if out is None or action not in ("lock", "unlock"):
        return _bad("Choose an output and lock or unlock.")
    if hub.state.locks.get(out, "U") == "L":
        return _bad("Another control surface holds that lock. Release it there.", 409)
    if not hub.lock(out, action == "lock"):
        return _bad("The Videohub is not connected.", 409)
    audit.note(request, "blackmagic.videohub_lock", f"{'Locked' if action == 'lock' else 'Unlocked'} a Videohub output",
               target=dev.cfg["name"], detail={"output": out + 1})
    return {"ok": True}


@router.post("/api/admin/blackmagic/videohub/presets", dependencies=MANAGE)
def admin_hub_preset_save(request: Request, payload: dict):
    """Save what the router routes right now as a preset (a preset of the same name is replaced)."""
    dev, hub, err = _hub(payload)
    if err:
        return err
    name = videohub.clean_label(payload.get("name"))
    if not name:
        return _bad("Give the preset a name.")
    routes = {str(o): i for o, i in sorted(hub.state.routing.items())}
    if not routes:
        return _bad("The Videohub has not said what it routes yet.", 409)
    items = devices()
    item = next((d for d in items if d["id"] == dev.cfg["id"]), None)
    if item is None:
        return _bad("That Videohub is not in the list.", 404)
    presets = item.get("presets", [])
    same = next((p for p in presets if p["name"].lower() == name.lower()), None)
    if same is None and len(presets) >= MAX_PRESETS:
        return _bad(f"A Videohub keeps at most {MAX_PRESETS} presets.")
    if same is not None:
        same.update(name=name, routes=routes)
    else:
        presets.append({"id": new_id(), "name": name, "routes": routes})
    item["presets"] = presets
    save_devices([clean_device(d) if d is item else d for d in items])
    audit.note(request, "blackmagic.videohub_preset_save", "Saved a Videohub route preset", target=name,
               detail={"device": dev.cfg["name"], "routes": len(routes)})
    return service.snapshot()


@router.delete("/api/admin/blackmagic/videohub/presets/{device_id}/{preset_id}", dependencies=MANAGE)
def admin_hub_preset_delete(request: Request, device_id: str, preset_id: str):
    items = devices()
    item = next((d for d in items if d["id"] == device_id and d["kind"] == "videohub"), None)
    preset = next((p for p in (item or {}).get("presets", []) if p["id"] == preset_id), None)
    if item is None or preset is None:
        return _bad("That preset is not saved.", 404)
    item["presets"] = [p for p in item["presets"] if p["id"] != preset_id]
    save_devices(items)
    audit.note(request, "blackmagic.videohub_preset_delete", "Deleted a Videohub route preset", target=preset["name"],
               detail={"device": item["name"]})
    return service.snapshot()


@router.post("/api/admin/blackmagic/videohub/presets/apply", dependencies=CONTROL)
def admin_hub_preset_apply(request: Request, payload: dict):
    dev, hub, err = _hub(payload)
    if err:
        return err
    saved = next((d for d in devices() if d["id"] == dev.cfg["id"]), {})
    preset = next((p for p in saved.get("presets", []) if p["id"] == payload.get("preset")), None)
    if preset is None:
        return _bad("That preset is not saved.", 404)
    sent, left_out = service.route(dev, {int(o): i for o, i in preset["routes"].items()})
    if not sent:
        return _bad("Nothing in that preset could be sent: the outputs are locked or no longer exist.", 409)
    audit.note(request, "blackmagic.videohub_preset", "Recalled a Videohub route preset", target=preset["name"],
               detail={"device": dev.cfg["name"], "sent": sent, "left_out": left_out})
    return {"ok": True, "sent": sent, "left_out": left_out}
