"""Signal map admin routes (#262). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audit, db
from ... import modules as registry
from .. import require
from . import patch

router = APIRouter()
VIEW = [Depends(require("signalmap.view"))]
MANAGE = [Depends(require("signalmap.manage"))]


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
    devices = [{"name": d["name"], "reachable": d.get("reachable"), "transmit": d["transmit"],
                "subscriptions": d.get("subscriptions")} for d in read()]
    return devices or None


@router.get("/api/admin/signalmap", dependencies=VIEW)
def state():
    """The saved patch (text and who saved it), each row joined with the live routing, and what the live routing has
    that the patch does not name."""
    saved = _saved()
    rows, problems = patch.parse(saved["source"]) if saved else ([], [])
    joined = patch.join(rows, _live())
    return {"saved": {"at": saved["saved_at"], "by": saved["saved_by"], "text": saved["source"]} if saved else None,
            "problems": problems, "dante": registry.using("dante.routing") is not None, **joined}


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
    user = getattr(request.state, "user", None)
    who = (getattr(user, "name", "") or getattr(user, "username", "")) if user else ""
    db.execute("insert into signalmap_patch (id, saved_at, saved_by, source) values (1, ?, ?, ?) "
               "on conflict(id) do update set saved_at = excluded.saved_at, saved_by = excluded.saved_by, "
               "source = excluded.source", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), who, raw))
    audit.note(request, "signalmap.save", f"Saved a patch list of {len(rows)} rows")
    return state()


@router.delete("/api/admin/signalmap/patch", dependencies=MANAGE)
def clear(request: Request):
    db.execute("delete from signalmap_patch where id = 1")
    audit.note(request, "signalmap.clear", "Removed the patch list")
    return state()
