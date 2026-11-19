"""YouTube admin routes (#252). Mounted by the module registry, so each answers 404 while the module is off."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ... import audit, config, oauth
from .. import require
from .schedule import NO_PC, Problem, clean_links, clean_schedule, pc_client, planner, stored, words
from .service import ACCOUNT, KEY, clean_settings, service, settings

router = APIRouter()
VIEW = require("youtube.view")
MANAGE = require("youtube.manage")

oauth.add_routes(router, "/api/admin/youtube/account", ACCOUNT, view=VIEW, manage=MANAGE)


@router.get("/api/admin/youtube", dependencies=[Depends(VIEW)])
async def admin_state() -> dict:
    return service.snapshot()


@router.put("/api/admin/youtube/settings", dependencies=[Depends(MANAGE)])
async def admin_settings(request: Request, payload: dict):
    """How often to check, and the church's own Google app (bring your own). A blank secret keeps the saved one;
    `clear_client` removes both. Changing the app does not change an account already connected."""
    cur = settings()
    new = dict(cur)
    for k in ("live_s", "idle_s"):
        if k in payload:
            new[k] = payload[k]
    if payload.get("clear_client") is True:
        new["client_id"], new["client_secret"] = "", ""
    else:
        if isinstance(payload.get("client_id"), str):
            new["client_id"] = payload["client_id"]
        if isinstance(payload.get("client_secret"), str) and payload["client_secret"].strip():
            new["client_secret"] = payload["client_secret"]
    new = clean_settings(new)
    if new["client_id"] and not new["client_id"].endswith(".apps.googleusercontent.com"):
        return JSONResponse({"error": "That is not a Google client ID: it ends in .apps.googleusercontent.com."},
                            status_code=400)
    config.save({KEY: {k: new[k] for k in ("client_id", "client_secret", "live_s", "idle_s")}})
    if (new["client_id"], new["client_secret"]) != (cur["client_id"], cur["client_secret"]):
        audit.note(request, "youtube.client", "Changed the Google app YouTube signs in with",
                   detail={"own_app": bool(new["client_id"])})
    service.poke()
    return service.snapshot()


@router.post("/api/admin/youtube/check", dependencies=[Depends(MANAGE)])
async def admin_check() -> dict:
    """Check now (costs quota, so only for the people who set it up)."""
    await service.check()
    return service.snapshot()


# ---------------------------------------------------------------- scheduled streams and Publishing links (#411)
PUBLISH = require("youtube.publish")


def _problem(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


@router.get("/api/admin/youtube/schedule", dependencies=[Depends(VIEW)])
async def schedule_state() -> dict:
    return planner.snapshot()


@router.put("/api/admin/youtube/schedule", dependencies=[Depends(MANAGE)])
async def schedule_save(request: Request, payload: dict):
    """The schedule's settings: on, privacy, stream key, title, days ahead, plan service types and weekly rules."""
    cur = stored()["schedule"]
    # days_from stays the stored one: the page always sends days counted from Sunday.
    new = clean_schedule({**cur, **{k: v for k, v in payload.items() if k in cur and k != "days_from"}})
    config.save({KEY: {"schedule": new}})
    if new["on"] != cur["on"]:
        audit.note(request, "youtube.schedule", "Turned scheduled YouTube streams " + ("on" if new["on"] else "off"))
    elif new != cur:
        audit.note(request, "youtube.schedule", "Changed the YouTube stream schedule")
    planner.poke()
    return planner.snapshot()


@router.post("/api/admin/youtube/schedule/check", dependencies=[Depends(MANAGE)])
async def schedule_check() -> dict:
    """Check now: list upcoming broadcasts, create any that are due, and send changed links."""
    await planner.run(asked=True)
    return planner.snapshot()


@router.get("/api/admin/youtube/streams", dependencies=[Depends(MANAGE)])
async def stream_keys():
    """The channel's stream keys, to bind new broadcasts to (1 unit of quota)."""
    try:
        return {"streams": await planner.list_streams()}
    except (Problem, oauth.Problem) as exc:
        return _problem(words(exc))


async def _pc(fn, *args):
    from .. import using

    client = pc_client()
    pcm = using("plan.api")
    if client is None or pcm is None:
        return _problem(NO_PC, 409)
    try:
        return await asyncio.to_thread(getattr(client, fn), *args)
    except pcm.PlanningCenterError as exc:
        return _problem(pcm.operator_message(exc), 502)


@router.get("/api/admin/youtube/publishing", dependencies=[Depends(MANAGE)])
async def publishing_channels():
    """Planning Center's Publishing channels, and the service types Relay follows (for scheduling from plans)."""
    channels = await _pc("channels")
    if isinstance(channels, JSONResponse):
        return channels
    types = await _pc("service_types")
    followed = set((config.get().get("planning_center") or {}).get("service_types") or [])
    types = [] if isinstance(types, JSONResponse) else [t for t in types if t["id"] in followed]
    return {"channels": channels, "service_types": types}


@router.get("/api/admin/youtube/publishing/{channel_id}/episodes", dependencies=[Depends(MANAGE)])
async def publishing_episodes(channel_id: str):
    if not re.fullmatch(r"\d{1,20}", channel_id):
        return _problem("That is not a Planning Center channel.", 404)
    eps = await _pc("episodes", channel_id)
    return eps if isinstance(eps, JSONResponse) else {"episodes": eps}


@router.put("/api/admin/youtube/links", dependencies=[Depends(MANAGE)])
async def links_save(request: Request, payload: dict):
    """The episodes Relay keeps the stream link in. What was last sent is kept for links that stay."""
    old = {x["id"]: x for x in stored()["links"]}
    raw = payload.get("links") if isinstance(payload.get("links"), list) else []
    for r in raw:
        if isinstance(r, dict) and r.get("id") in old:
            prev = old[r["id"]]
            same = (r.get("episode_id"), r.get("match"), r.get("video")) == \
                (prev["episode_id"], prev["match"], prev["video"])
            r["pushed"], r["pushed_at"] = (prev["pushed"], prev["pushed_at"]) if same else ("", 0.0)
        elif isinstance(r, dict):
            r["pushed"], r["pushed_at"] = "", 0.0
    links = clean_links(raw)
    config.save({KEY: {"links": links}})
    audit.note(request, "youtube.links", "Changed which Planning Center episodes get the YouTube link",
               detail={"episodes": [x["episode"] for x in links]})
    planner.poke()
    return planner.snapshot()


@router.post("/api/admin/youtube/links/{link_id}/send", dependencies=[Depends(PUBLISH)])
async def link_send(request: Request, link_id: str):
    """Send the link's broadcast to its episode now, even if it was sent already."""
    if not re.fullmatch(r"[a-z0-9]{1,16}", link_id):
        return _problem("That link is gone. Reload the page.", 404)
    out = await planner.push(link_id)
    if not out.get("ok"):
        return _problem(out["error"], 409)
    link = next((x for x in stored()["links"] if x["id"] == link_id), {})
    audit.note(request, "youtube.send", "Sent the YouTube link to Planning Center",
               target=link.get("episode", ""), detail={"video": out["video"]})
    return {**planner.snapshot(), "sent": out}
