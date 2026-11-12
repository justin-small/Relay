"""ProPresenter's routes: the admin pages (connection, control, slide view and lobby, the Planning Center link),
the public slide view at /stage when the operator allows it, and the lyrics and timers streams. Mounted by the
module registry, so every route answers 404 while the module is off."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from ... import config, engine as enginemod, modules as shared
from ...hub import sse_frame
from ...panels import timer_fields
from .. import event_stream, require
from . import propresenter, slidetags
from .pclink import MESSAGES as LINK_MESSAGES, LinkError, link as pc_link
from .ppcontrol import control as pp_control
from .pplobby import MESSAGES as LOBBY_MESSAGES, LobbyError, lobby as pp_lobby
from .ppview import view as pp_view
from .propresenter import follower as pp_follower, lyrics_feed, timers_feed

log = logging.getLogger("relay.propresenter")

router = APIRouter()




def _lyrics_stream(audience: str):
    """ProPresenter's current slide as lyrics, whole in each message (propresenter.LyricsFeed). Repeated at each
    heartbeat, so a panel's silence timer never fires on a quiet stream."""
    queue = lyrics_feed.subscribe(audience)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=shared.HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield sse_frame(lyrics_feed.payload(audience))
                    continue
                yield frame
        finally:
            lyrics_feed.unsubscribe(audience, queue)

    return event_stream(gen())




def _timers_stream(pid: str):
    """The ProPresenter timers a panel shows, whole in each message (propresenter.TimersFeed), for the panel `pid`
    ("" for the global settings). Repeated at each heartbeat, so a panel's silence timer never fires on a quiet
    stream and its local count is brought back to Relay's."""
    queue = timers_feed.subscribe(pid)

    async def gen():
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=shared.HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield sse_frame(timers_feed.payload(pid))
                    continue
                yield frame
        finally:
            timers_feed.unsubscribe(pid, queue)

    return event_stream(gen())


def _pp_body() -> dict:
    s = config.get()["propresenter"]
    return {"settings": s, "state": pp_follower.state(), "lyrics_readers": lyrics_feed.viewer_count(),
            "tags": slidetags.tags.state(), "lobby": pp_lobby.state()}


_PP_ERRORS = {
    "view_only": "View-only mode is on, so Relay did not change anything in ProPresenter.",
    "timeout": "ProPresenter did not answer in time.",
    "network": "Could not reach ProPresenter. Check the address and port, and that ProPresenter is running.",
    "bad": "That address answered, but not like ProPresenter.",
}


def _pp_error(exc: propresenter.ProError) -> JSONResponse:
    """Answer with a fixed message chosen by the error's kind; the detail goes to the log only, so no
    exception text (socket errors, ProPresenter's own reply) reaches the browser."""
    log.warning("ProPresenter: %s", exc)
    status = {"view_only": 409, "http": 502, "bad": 502, "timeout": 504, "network": 502}.get(exc.kind, 502)
    if exc.kind == "http":
        message = ("ProPresenter said not found: is Network switched on in its preferences?" if exc.status == 404
                   else f"ProPresenter refused the request ({int(exc.status)}).")
    else:
        message = _PP_ERRORS.get(exc.kind, "ProPresenter could not do that.")
    return JSONResponse({"error": message}, status_code=status)


@router.get("/api/admin/propresenter", dependencies=[Depends(require("propresenter.view"))])
async def admin_propresenter():
    return _pp_body()


@router.post("/api/admin/propresenter", dependencies=[Depends(require("propresenter.manage"))])
async def admin_propresenter_save(payload: dict):
    """Save the connection and switches. Only the keys sent change. A new address reconnects at once."""
    saved = config.get()["propresenter"]
    merged = {**saved, **{k: v for k, v in payload.items() if k in saved and k not in ("lyrics", "timers", "tags", "link", "lobby", "view")}}
    if isinstance(payload.get("lyrics"), dict):
        merged["lyrics"] = {**saved["lyrics"], **payload["lyrics"]}
    if "timers" in payload:
        problem = timer_fields(payload["timers"])[1]
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        merged["timers"] = propresenter.clean_timer_settings(payload["timers"], saved["timers"])
    if "tags" in payload:
        problem = propresenter.tag_fields(payload["tags"])[1]
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        merged["tags"] = propresenter.clean_tag_settings(payload["tags"], saved["tags"])
    for key, check, clean in (("lobby", propresenter.lobby_fields, propresenter.clean_lobby_settings),
                              ("view", propresenter.view_fields, propresenter.clean_view_settings)):
        if key in payload:
            problem = check(payload[key])[1]
            if problem:
                return JSONResponse({"error": problem}, status_code=400)
            merged[key] = clean(payload[key], saved[key])
    if "link" in payload:
        problem = propresenter.link_fields(payload["link"])[1]
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        merged["link"] = propresenter.clean_link_settings(payload["link"], saved["link"])
    if payload.get("host") and not propresenter.clean_host(payload["host"]):
        return JSONResponse({"error": "Enter just the machine's name or IP address, with no http:// or port."},
                            status_code=400)
    if payload.get("port") not in ("", None, 0) and "port" in payload and not propresenter.clean_port(payload["port"]):
        return JSONResponse({"error": "The port is a number from 1 to 65535."}, status_code=400)
    clean = propresenter.clean_stored(merged)
    if clean["enabled"] and not (clean["host"] and clean["port"]):
        return JSONResponse({"error": "Enter the machine's address and ProPresenter's port first. "
                                      "The port is in ProPresenter's Network preferences."}, status_code=400)
    config.save({"propresenter": clean}, replace=("propresenter",))
    log.info("ProPresenter settings saved (%s, view-only %s, slide tags %s, follow the plan %s, drive LIVE %s)",
             "on" if clean["enabled"] else "off", "on" if clean["view_only"] else "off",
             "on" if clean["tags"]["enabled"] else "off", "on" if clean["link"]["follow"] else "off",
             "on" if clean["link"]["drive"] else "off")
    await pp_follower.poke()
    lyrics_feed.refresh()
    timers_feed.refresh()
    enginemod.engine._push_status()   # what a panel's menu may offer from ProPresenter changed
    return _pp_body()


@router.post("/api/admin/propresenter/test", dependencies=[Depends(require("propresenter.manage"))])
async def admin_propresenter_test(payload: dict):
    """`GET /version` on the address in the form (saved or not). Changes nothing."""
    host = propresenter.clean_host(payload.get("host"))
    port = propresenter.clean_port(payload.get("port"))
    if not host or not port:
        return JSONResponse({"error": "Enter the machine's address and ProPresenter's port."}, status_code=400)
    client = propresenter.Client(host, port)
    try:
        info = await pp_follower.call(client.version)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return {"ok": True, "version": info}


@router.post("/api/admin/propresenter/discover", dependencies=[Depends(require("propresenter.manage"))])
async def admin_propresenter_discover():
    """ProPresenters announcing themselves on the LAN (mDNS). Nothing is saved or contacted."""
    return {"found": await pp_follower.call(propresenter.discover, 2.5)}


@router.post("/api/admin/propresenter/stage", dependencies=[Depends(require("propresenter.control"))])
async def admin_propresenter_stage(payload: dict):
    text = pp_follower.clean_message(payload.get("text"))
    if not text:
        return JSONResponse({"error": "Type the message to show."}, status_code=400)
    try:
        await pp_follower.set_stage_message(text)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return _pp_body()


@router.delete("/api/admin/propresenter/stage", dependencies=[Depends(require("propresenter.control"))])
async def admin_propresenter_stage_clear():
    try:
        await pp_follower.clear_stage_message()
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return _pp_body()


# --- ProPresenter control (#138 stage 2). Reads need propresenter.view, live control propresenter.control; the writes go through the one client,
# which refuses them in view-only mode (409). Errors answer through _pp_error or a fixed sentence.
async def _pp_run(fn, *args):
    try:
        data = await fn(*args)
    except PermissionError:
        return JSONResponse({"error": "That is not allowed by the operator-safe selection."}, status_code=403)
    except LookupError:
        return JSONResponse({"error": "No playlist is active or focused in ProPresenter. Trigger an item first."},
                            status_code=404)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return data if data is not None else {"ok": True}


@router.get("/api/admin/propresenter/slides", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_slides(uuid: str = ""):
    return await _pp_run(pp_control.slides, uuid or None)


@router.get("/api/admin/propresenter/thumbnail/{uuid}/{index}", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_thumbnail(uuid: str, index: int, v: str = ""):
    """A slide thumbnail, proxied and cached by Relay (key: presentation, index, change marker `v`)."""
    try:
        ctype, data = await pp_control.thumbnail(uuid, index, v)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/admin/propresenter/step", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_step(payload: dict):
    return await _pp_run(pp_control.step, payload.get("direction"))


@router.post("/api/admin/propresenter/next-presentation", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_next_presentation():
    return await _pp_run(pp_control.next_presentation)


@router.post("/api/admin/propresenter/item", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_item(payload: dict):
    """The next or previous playlist item (Shift+arrow keys on the control page)."""
    return await _pp_run(pp_control.step_item, payload.get("direction"))


@router.post("/api/admin/propresenter/screens", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_screens(payload: dict):
    """Audience or stage screens on or off. A write, so view-only refuses it like every other."""
    return await _pp_run(pp_control.set_screens, payload.get("kind"), payload.get("on"))


@router.post("/api/admin/propresenter/slide", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_slide(payload: dict):
    return await _pp_run(pp_control.trigger_slide, payload.get("uuid"), payload.get("index"))


@router.post("/api/admin/propresenter/clear", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_clear(payload: dict):
    return await _pp_run(pp_control.clear, payload.get("layer"))


@router.get("/api/admin/propresenter/playlists", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_playlists():
    return await _pp_run(pp_control.playlists)


@router.get("/api/admin/propresenter/playlists/{playlist}", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_playlist(playlist: str):
    return await _pp_run(pp_control.playlist_items, playlist)


@router.get("/api/admin/propresenter/playlists/{playlist}/diagnostics", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_playlist_diagnostics(playlist: str):
    return await _pp_run(pp_control.playlist_diagnostics, playlist)


@router.get("/api/admin/propresenter/playlists/{playlist}/items/{index}/cues", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_playlist_cues(playlist: str, index: int):
    return await _pp_run(pp_control.playlist_item_cues, playlist, index)


@router.get("/api/admin/propresenter/playlists/{playlist}/thumbnail/{index}/{cue}", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_playlist_thumbnail(playlist: str, index: int, cue: int, v: str = ""):
    """A playlist item's cue thumbnail, proxied and cached like the presentation ones (browsers never reach
    ProPresenter)."""
    try:
        ctype, data = await pp_control.playlist_thumbnail(playlist, index, cue, v)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/api/admin/propresenter/playlists/{playlist}/trigger", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_playlist_trigger(playlist: str, payload: dict):
    return await _pp_run(pp_control.trigger_playlist_item, playlist, payload.get("index"))


@router.get("/api/admin/propresenter/macros", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_macros(request: Request, all: bool = False):
    # The operator-safe selection is what an operator sees; every macro (the picker's list) is for whoever manages it.
    if all and not request.state.user.can("propresenter.manage"):
        return JSONResponse({"detail": "Not allowed"}, status_code=403)
    return await _pp_run(pp_control.macros, all)


@router.post("/api/admin/propresenter/macros/{macro}/trigger", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_macro_trigger(macro: str):
    return await _pp_run(pp_control.trigger_macro, macro)


@router.get("/api/admin/propresenter/looks", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_looks():
    return await _pp_run(pp_control.looks)


@router.post("/api/admin/propresenter/looks/{look}/trigger", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_look_trigger(look: str):
    return await _pp_run(pp_control.trigger_look, look)


@router.get("/api/admin/propresenter/timers", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_timers():
    return await _pp_run(pp_control.timers)


@router.post("/api/admin/propresenter/timers/{timer}/{op}", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_timer(timer: str, op: str):
    return await _pp_run(pp_control.timer, timer, op)


@router.get("/api/admin/propresenter/messages", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_messages():
    return await _pp_run(pp_control.messages)


@router.post("/api/admin/propresenter/messages/{message}/trigger", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_message_trigger(message: str, payload: dict):
    return await _pp_run(pp_control.trigger_message, message, payload.get("tokens"))


@router.post("/api/admin/propresenter/messages/{message}/clear", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_message_clear(message: str):
    return await _pp_run(pp_control.clear_message, message)


@router.post("/api/admin/propresenter/transport", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_transport(payload: dict):
    return await _pp_run(pp_control.go_to_time, payload.get("seconds"))


# --- Lobby announcements and the slide view (#138 stage 7, pplobby.py and ppview.py). Reads need propresenter.view, running the rotation propresenter.control; every
# write goes through propresenter.Client, which refuses it in view-only mode (409).
async def _lobby_run(fn, *args):
    try:
        data = await fn(*args)
    except LobbyError as exc:
        # A fixed sentence by key, never the exception's own text.
        return JSONResponse({"error": LOBBY_MESSAGES.get(exc.key, "The lobby rotation could not do that.")},
                            status_code=409)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return data


@router.get("/api/admin/propresenter/lobby", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_lobby():
    return pp_lobby.state()


@router.post("/api/admin/propresenter/lobby/start", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_lobby_start():
    return await _lobby_run(pp_lobby.start_rotation)


@router.post("/api/admin/propresenter/lobby/resume", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_lobby_resume():
    return await _lobby_run(pp_lobby.resume)


@router.post("/api/admin/propresenter/lobby/stop", dependencies=[Depends(require("propresenter.control"))])
async def admin_pp_lobby_stop(payload: dict):
    """Stop stepping; with `clear` true, also clear the announcements layer."""
    return await _lobby_run(pp_lobby.stop_rotation, payload.get("clear") is True)


async def _view_image(which: str, v: str):
    try:
        ctype, data = await pp_view.image(which, v)
    except LookupError:
        return JSONResponse({"error": "That slide is not up any more."}, status_code=404)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=300"})


@router.get("/api/admin/propresenter/live", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_live():
    return pp_view.snapshot("/api/admin/propresenter/live")


@router.get("/api/admin/propresenter/live/image/{which}", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_live_image(which: str, v: str = ""):
    return await _view_image(which, v)


# The same view without the admin login, only while `view.public` is on (off by default: slide images are
# licensed content). Off, every one of these is a 404, as though the route did not exist.
def _stage_public() -> bool:
    return bool(config.get()["propresenter"]["view"]["public"]) and propresenter.configured()


@router.get("/stage", response_class=HTMLResponse)
async def page_stage(request: Request):
    if not _stage_public():
        raise HTTPException(404, "Not found")
    return shared.templates.TemplateResponse(request, "propresenter/stage.html", {})


@router.get("/api/stage/live")
async def stage_live():
    if not _stage_public():
        raise HTTPException(404, "Not found")
    return pp_view.snapshot("/api/stage/live")


@router.get("/api/stage/live/image/{which}")
async def stage_live_image(which: str, v: str = ""):
    if not _stage_public():
        raise HTTPException(404, "Not found")
    return await _view_image(which, v)


# --- The Planning Center link (#138 stage 6, pclink.py). Reading it needs propresenter.view, changing it .manage. Matching only reads; the playlist
# route writes through propresenter.Client (view-only answers 409) and only ever to a playlist Relay made.
async def _link_run(fn, *args):
    try:
        data = await fn(*args)
    except LinkError as exc:
        # A fixed sentence by key, never the exception's own text.
        return JSONResponse({"error": LINK_MESSAGES.get(exc.key, "The Planning Center link could not do that.")},
                            status_code=409)
    except ValueError:
        return JSONResponse({"error": "That request was not valid."}, status_code=400)
    except propresenter.ProError as exc:
        return _pp_error(exc)
    return data


@router.get("/api/admin/propresenter/link", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_link():
    return pc_link.state()


@router.get("/api/admin/propresenter/link/presentations", dependencies=[Depends(require("propresenter.view"))])
async def admin_pp_link_presentations():
    """The presentations the last read found, for the operator's pairing picker (names and UUIDs only)."""
    return {"presentations": [{"uuid": c["uuid"], "name": c["name"]} for c in pc_link.pool]}


@router.post("/api/admin/propresenter/link/refresh", dependencies=[Depends(require("propresenter.manage"))])
async def admin_pp_link_refresh():
    """Read ProPresenter's playlists and libraries again and match the plan. Reads only."""
    async def go():
        await pc_link.refresh()
        return pc_link.state()
    return await _link_run(go)


@router.post("/api/admin/propresenter/link/mapping", dependencies=[Depends(require("propresenter.manage"))])
async def admin_pp_link_mapping(payload: dict):
    """Pair a plan item title with a presentation, or clear the pairing with an empty `uuid`."""
    async def go():
        uuid = str(payload.get("uuid") or "")
        if uuid and not propresenter.ID_RE.match(uuid):
            raise ValueError
        pc_link.set_mapping(str(payload.get("item") or ""), uuid, str(payload.get("name") or ""))
        return pc_link.state()
    return await _link_run(go)


@router.post("/api/admin/propresenter/link/playlist", dependencies=[Depends(require("propresenter.manage"))])
async def admin_pp_link_playlist():
    """Build (or rebuild) the Relay-owned playlist from the matched plan. On the operator's click only."""
    async def go():
        done = await pc_link.build_playlist()
        return {**done, "state": pc_link.state()}
    return await _link_run(go)
