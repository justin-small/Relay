"""Web page and YouTube links' admin routes. Mounted by the module registry, so each answers 404 while it is off."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ... import audit, config
from .. import require
from . import links

router = APIRouter()
VIEW = [Depends(require("signage_web.view"))]
MANAGE = [Depends(require("signage_web.manage"))]

KEY = "signage_web"


def _saved() -> list[dict]:
    return list(config.get()[KEY]["links"])


def _store(items: list[dict]) -> None:
    config.save({KEY: {"links": items}}, replace=(KEY,))


def _find(lid: str) -> dict:
    link = next((l for l in _saved() if l["id"] == lid), None)
    if link is None:
        raise HTTPException(404, "Not Found")
    return link


def _checked(link: dict) -> dict:
    """Ask the site (or YouTube) now; fill in the name from YouTube's title when none was typed."""
    result = links.check(link)
    title = result.pop("title", "")
    if title and (not link["name"] or link["name"] == link["url"]):
        link = {**link, "name": title[:100]}
    return {**link, "check": {"state": result["state"], "note": result["note"],
                              "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}}


def _bad(exc: links.Invalid) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=400)


@router.get("/api/admin/signage-web", dependencies=VIEW)
def admin_list():
    return {"links": _saved()}


@router.post("/api/admin/signage-web/links", dependencies=MANAGE)
def admin_add(request: Request, payload: dict):
    items = _saved()
    if len(items) >= links.MAX_LINKS:
        return JSONResponse({"error": f"At most {links.MAX_LINKS} links can be saved."}, status_code=400)
    try:
        link = links.clean_link({"url": payload.get("url"), "name": payload.get("name")}, {l["id"] for l in items})
    except links.Invalid as exc:
        return _bad(exc)
    link = _checked(link)
    link["name"] = link["name"] or link["url"]
    _store(items + [link])
    audit.note(request, "signage_web.add", "Added a signage web page or YouTube link", target=link["name"],
               detail={"kind": link["kind"]})
    return link


@router.put("/api/admin/signage-web/links/{lid}", dependencies=MANAGE)
def admin_edit(lid: str, request: Request, payload: dict):
    old = _find(lid)
    try:
        link = links.clean_link({"id": lid, "url": payload.get("url", old["url"]),
                                 "name": payload.get("name", old["name"])})
    except links.Invalid as exc:
        return _bad(exc)
    if link["url"] != old["url"]:
        link = _checked(link)
    else:
        link["check"] = old["check"]
    link["name"] = link["name"] or link["url"]
    _store([link if l["id"] == lid else l for l in _saved()])
    audit.note(request, "signage_web.edit", "Changed a signage web page or YouTube link", target=link["name"])
    return link


@router.delete("/api/admin/signage-web/links/{lid}", dependencies=MANAGE)
def admin_delete(lid: str, request: Request):
    old = _find(lid)
    _store([l for l in _saved() if l["id"] != lid])
    audit.note(request, "signage_web.delete", "Removed a signage web page or YouTube link", target=old["name"])
    return {"ok": True}


@router.post("/api/admin/signage-web/links/{lid}/check", dependencies=MANAGE)
def admin_check(lid: str, request: Request):
    link = _checked(_find(lid))
    _store([link if l["id"] == lid else l for l in _saved()])
    audit.note(request, "signage_web.check", "Re-checked a signage web page or YouTube link", target=link["name"],
               detail={"result": link["check"]["state"]})
    return link
