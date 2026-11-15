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
    return templates.TemplateResponse(request, "crew/crew.html", {
        "app_name": s["name"], "show_plan": s["show_plan"], "user": got,
        "tools": registry.user_nav(got),
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
