"""The crew page's routes. Mounted by the module registry, so each answers 404 while the module is off, and the
registry's panel-only rule (Module.panel_prefixes) answers 404 for every /crew path on the viewer port.

Public on purpose, because a browser fetches them without the sign-in cookie: the manifest, the service worker and
its offline page. They carry nothing but the page's name and icons."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ... import audit, config
from .. import modules as registry, require
from . import people

router = APIRouter()
VIEW = [Depends(require("crew.view"))]
MANAGE = [Depends(require("crew.manage"))]
USE = [Depends(require("crew.use"))]

KEY = "crew"
HERE = Path(__file__).resolve().parent
NO_STORE = {"Cache-Control": "no-store"}
ICONS = [
    {"src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
]


def _settings() -> dict:
    return config.get()[KEY]


def _pkg():
    from .. import page_gate, templates

    return page_gate, templates


@router.get("/crew", response_class=HTMLResponse)
async def crew_page(request: Request):
    """The crew page: sign-in for a signed-out visitor, "not allowed" for a role without crew.use."""
    gate, templates = _pkg()
    got = await asyncio.to_thread(gate, request, "crew.use")
    if isinstance(got, Response):
        return got
    s = _settings()
    try:
        role = await asyncio.to_thread(people.profile, got.id)
        role = role["role"]
    except Exception:
        role = ""      # the role is a label: the page works without it
    return templates.TemplateResponse(request, "crew/crew.html", {
        "app_name": s["name"], "show_plan": s["show_plan"], "user": got, "crew_role": role,
        "tools": registry.user_nav(got), "tabs": registry.crew_nav(got),
        "module_scripts": registry.crew_scripts(got),
    }, headers=NO_STORE)


@router.get("/crew/_/manifest.webmanifest")
def crew_manifest():
    name = _settings()["name"]
    return JSONResponse({
        "name": name, "short_name": name, "id": "/crew", "start_url": "/crew", "scope": "/crew",
        "display": "standalone", "background_color": "#000000", "theme_color": "#000000", "icons": ICONS,
    }, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/crew/_/sw.js")
def crew_service_worker():
    """The service worker. Its default scope would be /crew/_/, so Service-Worker-Allowed widens it to the page."""
    return Response((HERE / "static" / "sw.js").read_bytes(), media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/crew"})


@router.get("/crew/_/offline", response_class=HTMLResponse)
def crew_offline():
    return HTMLResponse((HERE / "templates" / "offline.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-cache"})


@router.get("/crew/api/plan", dependencies=USE)
def crew_plan():
    """Today's plan for the page, from Planning Center when that module is on and the setting allows it. Titles
    and times only: the people, mics and notes stay on the production team's screen."""
    if not _settings()["show_plan"] or not registry.enabled("planning_center"):
        return {"available": False, "plan": None}
    from ..planning_center.routes import rundown_now

    msg = rundown_now("", "")
    if not msg.get("plan"):
        return {"available": True, "plan": None}
    return {"available": True, "plan": {
        "title": msg["plan"]["title"], "dates": msg["plan"]["dates"],
        "time": (msg.get("time") or {}).get("hhmm"), "zone": msg.get("zone"),
        "current": (msg.get("header") or {}).get("current"),
        "items": [{"title": i["title"], "kind": i["kind"], "planned": i["planned"], "state": i["state"]}
                  for i in msg["items"]],
    }}


@router.get("/api/admin/crew", dependencies=VIEW)
def admin_get():
    return _settings()


@router.post("/api/admin/crew", dependencies=MANAGE)
def admin_save(request: Request, payload: dict):
    from . import MODULE

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, "Give the crew page a name.")
    show = payload.get("show_plan")
    if not isinstance(show, bool):
        raise HTTPException(400, "Choose whether the page shows the plan.")
    clean = MODULE.clean({"name": name, "show_plan": show})
    config.save({KEY: clean}, replace=(KEY,))
    audit.note(request, "crew.change", "Changed the crew page's setup", detail={"show_plan": clean["show_plan"]})
    return _settings()


# -- roles and Planning Center names (#227) --------------------------------------------------------------------
def _people_call(fn, *args):
    from ... import db

    try:
        return fn(*args)
    except people.Invalid as exc:
        raise HTTPException(exc.status, exc.message)
    except db.Unavailable:
        raise HTTPException(503, "The user database is not available.")


@router.get("/api/admin/crew/people", dependencies=VIEW)
def people_get():
    return {"people": _people_call(people.people_list),
            "planning_center": registry.enabled("planning_center")}


@router.post("/api/admin/crew/people/scan", dependencies=MANAGE)
def people_scan(request: Request):
    """Match names now, instead of waiting for Planning Center to change."""
    if not registry.enabled("planning_center"):
        raise HTTPException(400, "Turn Planning Center on to match names.")
    from . import MODULE

    MODULE._seen = ""
    _people_call(MODULE._scan)
    return people_get()


@router.put("/api/admin/crew/people/{user_id}/role", dependencies=MANAGE)
def people_role(request: Request, user_id: int, payload: dict):
    role = _people_call(people.set_role, user_id, payload.get("role"))
    audit.note(request, "crew.role", "Set a crew role", target=user_id, detail={"role": role})
    return {"role": role}


@router.post("/api/admin/crew/people/{user_id}/name", dependencies=MANAGE)
def people_name(request: Request, user_id: int, payload: dict):
    """Accept or dismiss the Planning Center spelling suggested for a person's name."""
    action = payload.get("action")
    if action == "accept":
        name = _people_call(people.accept_name, user_id, request.state.user)
        audit.note(request, "crew.name", "Accepted the Planning Center spelling of a name", target=user_id)
        return {"name": name}
    if action == "dismiss":
        _people_call(people.dismiss_name, user_id)
        return {"ok": True}
    raise HTTPException(400, "Choose accept or dismiss.")


@router.delete("/api/admin/crew/people/{user_id}/match", dependencies=MANAGE)
def people_unlink(request: Request, user_id: int):
    _people_call(people.unlink, user_id)
    audit.note(request, "crew.unmatch", "Removed a Planning Center match", target=user_id)
    return {"ok": True}
