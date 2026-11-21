"""Signal map admin routes (#262). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audit, db, netinfo
from ... import modules as registry
from .. import require
from . import patch

router = APIRouter()
VIEW = [Depends(require("signalmap.view"))]
MANAGE = [Depends(require("signalmap.manage"))]


def _store(request: Request, raw: str, rows: list, what: str) -> None:
    user = getattr(request.state, "user", None)
    who = (getattr(user, "name", "") or getattr(user, "username", "")) if user else ""
    db.execute("insert into signalmap_patch (id, saved_at, saved_by, source) values (1, ?, ?, ?) "
               "on conflict(id) do update set saved_at = excluded.saved_at, saved_by = excluded.saved_by, "
               "source = excluded.source", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), who, raw))
    audit.note(request, "signalmap.save", f"{what} of {len(rows)} rows")


def _saved() -> dict | None:
    try:
        rows = db.query("select saved_at, saved_by, source from signalmap_patch where id = 1")
    except db.Unavailable:
        raise HTTPException(503, "The database is not available yet.")
    return rows[0] if rows else None


def _live() -> list[dict] | None:
    """The Dante devices as Dante last read them, or None while Dante is off or has not scanned."""
    read = registry.using("dante.routing")
    if read is None:
        return None
    devices = [{"name": d["name"], "ips": d.get("ips") or [], "reachable": d.get("reachable"), "transmit": d["transmit"],
                "subscriptions": d.get("subscriptions")} for d in read()]
    return devices or None


def _desks() -> list[dict]:
    """Desks whose show-file patch the sound desk module holds (empty while it is off)."""
    read = registry.using("patch.audio_mixers")
    return read() if read else []


@router.get("/api/admin/signalmap", dependencies=VIEW)
def state():
    """The saved patch (text and who saved it), each row joined with the live routing, and what the live routing has
    that the patch does not name."""
    saved = _saved()
    rows, problems = patch.parse(saved["source"]) if saved else ([], [])
    devices = _live()
    unifi = registry.using("gear.unifi")
    net = patch.switch_ports(devices or [], unifi["list"]()["devices"] if unifi else None)
    local = frozenset(a for p in netinfo.interfaces() for a in p.addresses)
    joined = patch.join(rows, devices, net, local)
    return {"saved": {"at": saved["saved_at"], "by": saved["saved_by"], "text": saved["source"]} if saved else None,
            "problems": problems, "dante": registry.using("dante.routing") is not None, "unifi": unifi is not None,
            "desks": [{"id": d["desk"], "name": d["name"], "model": d["model"], "file": d.get("file", ""),
                       "channels": sum(1 for r in d.get("inputs") or [] if r.get("named"))} for d in _desks()],
            **joined}


@router.put("/api/admin/signalmap/patch", dependencies=MANAGE)
async def save(request: Request):
    """Replace the patch list: {"text": "CH,NAME,PORT,SOCKET,UPSTREAM\\n1,Kick,..."}."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    raw = body.get("text") if isinstance(body, dict) else None
    if not isinstance(raw, str) or len(raw) > patch.MAX_TEXT:
        raise HTTPException(400, "Send the patch list as text.")
    rows, problems = patch.parse(raw)
    if not rows:
        raise HTTPException(400, "Relay found no rows with a name. " + " ".join(problems[:3]))
    _store(request, raw, rows, "Saved a patch list")
    return state()


@router.post("/api/admin/signalmap/import", dependencies=MANAGE)
async def import_desk(request: Request):
    """Replace the patch list with the one read from a desk's show file: {"desk": "<desk id>"}."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    want = body.get("desk") if isinstance(body, dict) else None
    desk = next((d for d in _desks() if d["desk"] == want), None)
    if desk is None:
        raise HTTPException(404, "That desk has no patch read from a show file. Read one on the Sound desk page.")
    raw = patch.from_desk(desk)
    rows, _ = patch.parse(raw)
    if not rows:
        raise HTTPException(400, "That show has no named input channels.")
    _store(request, raw, rows, f"Imported {desk['name']}'s patch")
    return state()


@router.delete("/api/admin/signalmap/patch", dependencies=MANAGE)
def clear(request: Request):
    db.execute("delete from signalmap_patch where id = 1")
    audit.note(request, "signalmap.clear", "Removed the patch list")
    return state()
