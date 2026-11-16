"""Lighting admin routes (#377). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ... import audit, config
from .. import require
from .backend import BackendError
from .service import KEY, clean_pages, clean_settings, new_key, service

router = APIRouter()
VIEW = [Depends(require("lighting.view"))]
CONTROL = [Depends(require("lighting.control"))]
MANAGE = [Depends(require("lighting.manage"))]


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


async def _do(verb: str, cue_id, request, *, page_only: bool, args: tuple = (), detail: str = "",
              by_name: bool = False, source: str = "") -> dict:
    """Press or release one cue, once. The audit note names the page and the cue ("Lighting: Worship, Verse"); with
    no request, `source` says where a system press came from ("... (from a ProPresenter slide tag)")."""
    if not isinstance(cue_id, str):
        return {"ok": False, "error": "Choose a cue.", "status": 400}
    if by_name:
        named = service.page_cue_named(cue_id)
        if named is None:
            return {"ok": False, "error": "No cue with that name is on a lighting page.", "status": 404}
        cue_id = named[1]["id"]
    found = await service.find(cue_id, page_only=page_only)
    if found is None:
        return {"ok": False, "error": "That cue is not on a lighting page.", "status": 404}
    label, cue_id = found
    try:
        await service.act(verb, cue_id, *args)
    except BackendError as e:
        return {"ok": False, "error": str(e), "status": 502}
    if request is not None:
        audit.note(request, f"lighting.{verb}", f"Lighting: {label}{detail}", target=cue_id)
    elif source:
        audit.record(f"lighting.{verb}", f"Lighting: {label}{detail} (from {source})", target=cue_id)
    return {"ok": True}


async def fire(cue_id: str, request=None, *, by_name: bool = False, source: str = "") -> dict:
    """The hook other modules reach through `app.modules.fire_cue`: only a cue on a page, never the controller's
    whole list."""
    r = await _do("press", cue_id, request, page_only=True, by_name=by_name, source=source)
    r.pop("status", None)
    return r


def _reply(r: dict):
    if r["ok"]:
        return {"ok": True}
    return _bad(r["error"], r["status"])


@router.get("/api/admin/lighting", dependencies=VIEW)
async def admin_state():
    return await service.snapshot()


@router.put("/api/admin/lighting/settings", dependencies=MANAGE)
async def admin_settings(request: Request, payload: dict):
    cur = service.settings()
    new = clean_settings({**cur, **{k: payload[k] for k in ("backend", "host", "port", "functions", "console", "fade") if k in payload},
                          "password": payload["password"] if isinstance(payload.get("password"), str)
                          and payload["password"] else cur["password"]})
    if isinstance(payload.get("host"), str) and payload["host"].strip() and not new["host"]:
        return _bad("That is not an address: use the lighting computer's IP address, like 192.168.1.50.")
    config.save({KEY: new})
    await service.restart()
    audit.note(request, "lighting.settings", "Changed the lighting program", target=new["backend"] or "(none)")
    return await service.snapshot()


@router.put("/api/admin/lighting/pages", dependencies=MANAGE)
async def admin_pages(request: Request, payload: dict):
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return _bad("Send the list of pages.")
    cur = service.settings()
    keys = {p["id"]: p["key"] for p in cur["pages"]}
    on = {p["id"]: p["key_on"] for p in cur["pages"]}
    # The key and its switch are changed only by the routes below, never by what the page editor sends back.
    sent = [{**p, "key_on": on.get(p.get("id"), False)} if isinstance(p, dict) else p for p in pages]
    config.save({KEY: {**cur, "pages": clean_pages(sent, keys)}})
    audit.note(request, "lighting.pages", "Changed the lighting cue pages")
    return await service.snapshot()


def _page(page_id: str) -> dict | None:
    return next((p for p in service.settings()["pages"] if p["id"] == page_id), None)


def _link(request: Request, page: dict) -> str:
    return f"{request.url.scheme}://{request.url.netloc}/screen?lighting={page['id']}&key={page['key']}"


@router.put("/api/admin/lighting/pages/{page_id}/screen", dependencies=MANAGE)
async def admin_screen(page_id: str, request: Request, payload: dict):
    """Switch a page's screen on or off, or rotate its key (the old link stops working at once). Answers with the link
    to open on a tablet: the only place the key is ever sent."""
    cur = service.settings()
    page = next((p for p in cur["pages"] if p["id"] == page_id), None)
    if page is None:
        return _bad("There is no such page.", 404)
    on = payload.get("on") is True
    if on and (not page["key"] or payload.get("rotate") is True):
        page["key"] = new_key()
    elif payload.get("rotate") is True:
        return _bad("Turn the screen on first.")
    page["key_on"] = on
    config.save({KEY: cur})
    audit.note(request, "lighting.screen", f"{'Turned on' if on else 'Turned off'} the lighting screen for {page['name']}"
               + (" with a new key" if payload.get("rotate") is True else ""), target=page_id)
    return {"on": on, "link": _link(request, page) if on else ""}


@router.get("/api/admin/lighting/pages/{page_id}/link", dependencies=MANAGE)
async def admin_screen_link(page_id: str, request: Request):
    page = _page(page_id)
    if page is None:
        return _bad("There is no such page.", 404)
    return {"on": page["key_on"], "link": _link(request, page) if page["key_on"] else ""}


@router.post("/api/admin/lighting/test", dependencies=MANAGE)
async def admin_test():
    if service.backend is None:
        return {"ok": False, "detail": "Choose the lighting program first."}
    try:
        return await service.backend.test()
    except Exception:
        return {"ok": False, "detail": "The lighting program did not answer."}


@router.post("/api/admin/lighting/press", dependencies=CONTROL)
async def admin_press(request: Request, payload: dict):
    return _reply(await _do("press", payload.get("cue"), request, page_only=False))


@router.post("/api/admin/lighting/release", dependencies=CONTROL)
async def admin_release(request: Request, payload: dict):
    return _reply(await _do("release", payload.get("cue"), request, page_only=False))


@router.post("/api/admin/lighting/level", dependencies=CONTROL)
async def admin_level(request: Request, payload: dict):
    level = payload.get("level")
    if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
        return _bad("Choose a level from 0 to 100.")
    return _reply(await _do("set_level", payload.get("cue"), request, page_only=False, args=(level,),
                            detail=f" to {level}%"))


# ====================================================================================================== /screen
# A cue page on a browser screen: plain HTTP with no sign-in, so a tablet at the lighting desk needs nothing but its
# link. The link's per-page key is the whole authority: it can press the cues on that page and nothing else.
WINDOW_S = 10.0
MAX_PRESSES = 20          # per client address per window
MAX_BAD_KEYS = 8          # wrong keys per client address per window; a stranger is slowed before anything is logged
_hits: dict[str, list[float]] = {}
_bad_hits: dict[str, list[float]] = {}


def _limited(table: dict, who: str, limit: int) -> bool:
    """True when `who` already used `limit` presses in the window; otherwise counts this one."""
    now = time.monotonic()
    if len(table) > 2000:
        for k in [k for k, v in table.items() if not v or now - v[-1] > WINDOW_S]:
            table.pop(k, None)
    recent = [t for t in table.get(who, []) if now - t < WINDOW_S]
    if len(recent) >= limit:
        table[who] = recent
        return True
    table[who] = recent + [now]
    return False


def _who(request: Request) -> str:
    return request.client.host if request.client else ""


def _locked(request: Request) -> bool:
    return len(_bad_hits.get(_who(request), [])) >= MAX_BAD_KEYS and \
        time.monotonic() - _bad_hits[_who(request)][-1] < WINDOW_S


def _bad_key(request: Request) -> None:
    who = _who(request)
    now = time.monotonic()
    _bad_hits[who] = [t for t in _bad_hits.get(who, []) if now - t < WINDOW_S][-MAX_BAD_KEYS:] + [now]


def _checked(request: Request, page_id: str, key: str):
    """(page, None) when the key is right, else (None, an answer). Wrong keys are counted, never logged."""
    if _locked(request):
        return None, JSONResponse({"error": "slow"}, status_code=429)
    page = service.screen_page(page_id, key) if isinstance(page_id, str) and isinstance(key, str) else None
    if page is None:
        _bad_key(request)
        return None, JSONResponse({"error": "refused"}, status_code=404)
    return page, None


async def screen_page(request: Request):
    """/screen?lighting=<page>&key=<key>: the page of buttons. The same plain 404 whether the page does not exist,
    its screen is off or the key is wrong."""
    from .. import templates

    page, err = _checked(request, request.query_params.get("lighting", ""), request.query_params.get("key", ""))
    if page is None:
        return err if err.status_code == 429 else HTMLResponse("Not found", status_code=404)
    cues = [{k: c[k] for k in ("id", "name", "label", "shape", "color", "text_color", "w", "h", "x", "y", "fw", "fh")}
            for c in page["cues"]]
    return templates.TemplateResponse(request, "lighting/screen.html", {
        "page": {"id": page["id"], "name": page["name"], "free": page["free"], "cues": cues},
        "key": page["key"]}, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/lighting/_/state/{page_id}")
async def screen_state(page_id: str, request: Request, key: str = ""):
    page, err = _checked(request, page_id, key)
    if page is None:
        return err
    state = {}
    b = service.backend
    if b is not None and b.can_report_state:
        try:
            lit = await b.state()
            state = {c["id"]: bool(lit.get(c["id"])) for c in page["cues"]}
        except Exception:
            state = {}
    return JSONResponse({"state": state}, headers={"Cache-Control": "no-store"})


@router.post("/lighting/_/press/{page_id}")
async def screen_press(page_id: str, request: Request, payload: dict, key: str = ""):
    """One press from a screen. Answers {"ok": true} or {"error": "unreachable" | "refused" | "slow"}: a short code,
    never the lighting program's own words. It is logged only after the key checked out."""
    page, err = _checked(request, page_id, key)
    if page is None:
        return err
    cue = payload.get("cue")
    if not isinstance(cue, str) or not any(c["id"] == cue for c in page["cues"]):
        return JSONResponse({"error": "refused"}, status_code=404)
    if _limited(_hits, _who(request), MAX_PRESSES):
        return JSONResponse({"error": "slow"}, status_code=429)
    r = await _do("press", cue, None, page_only=True, source=f"the {page['name']} screen")
    if r["ok"]:
        return {"ok": True}
    return JSONResponse({"error": "refused" if r["status"] < 500 else "unreachable"}, status_code=r["status"])
