"""Alert events' admin routes. Mounted by the module registry, so each answers 404 while it is off."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import audit, config
from ...alerts import alerts
from .. import require
from . import events

router = APIRouter()
VIEW = [Depends(require("alert_events.view"))]
MANAGE = [Depends(require("alert_events.manage"))]

KEY = "alert_events"


@router.get("/api/admin/alert-events", dependencies=VIEW)
def admin_get():
    from . import MODULE

    return {**config.get()[KEY], "events": list(events.EVENTS), "last_error": MODULE.last_error}


@router.post("/api/admin/alert-events", dependencies=MANAGE)
def admin_save(request: Request, payload: dict):
    try:
        clean = events.validate(payload)
    except events.Invalid as exc:
        raise HTTPException(400, exc.message)
    config.save({KEY: clean}, replace=(KEY,))
    audit.note(request, "alert_events.change", "Changed where alert events are sent",
               detail={"webhook": clean["webhook"]["enabled"], "osc": clean["osc"]["enabled"]})
    return config.get()[KEY]


@router.post("/api/admin/alert-events/test", dependencies=MANAGE)
async def admin_test(request: Request):
    errors = await asyncio.to_thread(events.deliver, config.get()[KEY], events.test_event())
    audit.note(request, "alert_events.test", "Sent a test alert event", detail={"ok": not errors})
    return {"ok": not errors, "detail": errors[0] if errors else "A test event was sent."}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "button"


@router.get("/api/admin/alert-events/state", dependencies=VIEW)
def admin_state():
    """Alert state for Bitfocus Companion (#219): `variables` are flat strings a Companion module can publish as
    variables, and `feedbacks` say which buttons are in each state, for a button to light on. Read with an API
    token, like every admin route."""
    from . import MODULE

    rows = alerts.state()["buttons"]
    variables: dict[str, str] = {"alerts_on": str(sum(1 for r in rows if r["state"] == "on")),
                                 "last_event": str(MODULE.last.get("event", "")),
                                 "last_button": str(MODULE.last.get("button", MODULE.last.get("text", ""))),
                                 "last_from": str(MODULE.last.get("from", "")),
                                 "last_reply": str(MODULE.last.get("reply", ""))}
    feedbacks: dict[str, list[str]] = {"alert_on": [], "alert_seen": [], "alert_missed": [], "alert_replied": []}
    used: set[str] = set()
    for r in rows:
        slug = _slug(r["name"])
        while slug in used:
            slug += "_"
        used.add(slug)
        variables[f"{slug}_state"] = r["state"]
        variables[f"{slug}_from"] = r.get("from", "")
        variables[f"{slug}_seen_on"] = r.get("seen_on", "")
        variables[f"{slug}_reply"] = r.get("reply", "")
        if r["state"] == "on":
            feedbacks["alert_on"].append(r["id"])
        elif r["state"] == "seen":
            feedbacks["alert_seen"].append(r["id"])
            if r.get("reply"):
                feedbacks["alert_replied"].append(r["id"])
        elif r["state"] == "missed":
            feedbacks["alert_missed"].append(r["id"])
    return {"buttons": [{"id": r["id"], "name": r["name"], "state": r["state"]} for r in rows],
            "variables": variables, "feedbacks": feedbacks}
