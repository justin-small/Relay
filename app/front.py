"""The app behind the Rust server (#653, part of #650).

While Relay moves from Python to Rust route by route, the Rust server (`native/relay-server`) owns the sockets: the viewer port,
the HTTPS panel port, each module's own port, the panel certificate and the mDNS announcement. This app then runs on one loopback
port over plain HTTP, started with `python run.py --upstream-port N`, and the two exchange what only each knows:

  GET  /api/internal/front   what only the modules know: each listener that is on (port, prefixes, traffic class) and every
                             prefix reserved for a listener or for the HTTPS panel, so the Rust server can open and gate them.
  POST /api/internal/front   what only the server knows: the ports really bound, the notices about them, where each listener
                             binds and the panel certificate, so links, the admin page and the Network page say what is true.

Both need `X-Relay-Front: <RELAY_FRONT_TOKEN>`, the same secret the server is started with; without a token set they answer 404
and nothing here is on. The server forwards each request with `X-Relay-Port`, the port the client really reached, and `adopt_port`
puts it back in the request's scope, so every rule that asks "which socket did this arrive on" (`serving.py`, the gate in
`app/main.py`, the intercom and parent-paging checks) answers as it does when this app owns the sockets itself.
"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request

from . import config, serving, tls

FRONT_HEADER = "x-relay-front"
PORT_HEADER = b"x-relay-port"
_LOOPBACK = {"127.0.0.1", "::1"}

router = APIRouter()


def token() -> str:
    return (os.environ.get("RELAY_FRONT_TOKEN") or "").strip()


def enabled() -> bool:
    """True when this app sits behind the Rust server."""
    return bool(token())


def adopt_port(scope) -> None:
    """Put the port the client really reached back in the request's scope (only from a loopback peer, like X-Forwarded-For)."""
    if not enabled() or scope.get("type") not in ("http", "websocket"):
        return
    client = scope.get("client") or ()
    if not client or client[0] not in _LOOPBACK:
        return
    for key, value in scope.get("headers") or ():
        if key == PORT_HEADER:
            try:
                port = int(value)
            except ValueError:
                return
            if 1 <= port <= 65535:
                host = (scope.get("server") or ("127.0.0.1", 0))[0]
                scope["server"] = (host, port)
            return


def _authorised(request: Request) -> None:
    given = request.headers.get(FRONT_HEADER, "")
    if not enabled() or not hmac.compare_digest(given.encode(), token().encode()):
        # Not "forbidden": an app with no front door has no such route.
        raise HTTPException(status_code=404, detail="Not found")


def plan() -> dict:
    """The listeners of the modules that are on, and the prefixes every module reserves (the registry's own answers)."""
    from . import netroute
    from .modules import modules

    classes = netroute.classes()
    listeners = []
    for mod, lst, port in modules.listeners(config.get()):
        traffic = classes.get(lst.traffic, (None, None))[1]
        listeners.append({
            "key": f"{mod.manifest.id}.{lst.name}",
            "what": f"{mod.manifest.name} ({lst.label})",
            "traffic": lst.traffic,
            "traffic_label": traffic.label if traffic else lst.traffic,
            "policy": traffic.default_policy if traffic else "fallback",
            "port": port,
            "prefixes": list(lst.prefixes),
        })
    # What each module adds to the role table, so the server's `can` and `/api/admin/me` know the permissions of modules it does
    # not have yet (#719); every module, on or off, as the Users page lists them.
    permissions = [{"id": p.id, "label": p.label, "group": p.group, "min_role": p.min_role}
                   for mod in modules.all() for p in mod.permissions]
    return {"listeners": listeners, "permissions": permissions, "listener_prefixes": list(modules.listener_prefixes()),
            "panel_prefixes": list(modules.panel_prefixes()),
            # What the server last reported, so a check can see the round trip (None until it has).
            "published": {"viewer_port": serving.viewer_port(0) or None, "admin_port": serving.admin_port(0) or None,
                          "fingerprint": tls.panel.describe()["fingerprint"]}}


def apply(report: dict) -> None:
    """Take what the server really bound, as `run.py` publishes it when this app owns the sockets."""
    serving.publish(int(report["viewer_port"]), int(report["admin_port"]), [str(n) for n in report.get("notices") or []])
    groups: list[serving.Group] = []
    listeners: dict[int, tuple[str, ...]] = {}
    for g in report.get("groups") or []:
        # No sockets here: `servers` carries the hosts the server listens on, so the Network page can say where each one is.
        groups.append(serving.Group(key=str(g["key"]), label=str(g["label"]), traffic=g.get("traffic"),
                                    configured=str(g.get("configured") or ""), port=int(g["port"]),
                                    secure=bool(g.get("secure")), servers={str(h): None for h in g.get("hosts") or []},
                                    notes=[str(n) for n in g.get("notes") or []]))
        if g.get("prefixes"):
            listeners[int(g["port"])] = tuple(str(p) for p in g["prefixes"])
    serving.publish_groups(groups)
    serving.publish_listeners(listeners)
    cert = report.get("certificate") or {}
    tls.panel.adopt(str(cert.get("fingerprint") or ""), [str(n) for n in cert.get("names") or []],
                    str(cert.get("expires") or ""), cert.get("changed"))


# -- what the Rust server asks of this app's modules for the audio bus (#657) ----------------------------------------------------
# The server owns the bus, the channels and the routes. A module that is still Python owns its gear, so the server asks here which
# audio sources and outputs are registered, which modules apply routes (and how), and to apply, read and rename on the gear.
def _route_apis() -> dict[str, dict]:
    from .modules import modules as registry

    return {pid: api for pid, api in registry.using_all("routes.").items() if isinstance(api, dict) and callable(api.get("apply"))}


@router.get("/api/internal/audio/registry")
async def audio_registry(request: Request):
    """The audio sources and outputs modules registered, for the Audio bus page's pickers."""
    _authorised(request)
    from . import audio

    return {"sources": [{"id": s.id, "label": s.label} for s in audio.sources()],
            "outputs": [{"id": o.id, "label": o.label} for o in audio.outputs()]}


# -- what the Rust server asks of this app's modules for the live captions (#659) -------------------------------------------------
# The server owns the caption engine and the stream's Start and Stop. What only the modules here know goes into the pages it serves
# (the top-bar pills, each module's health, the schedule, the stream-watching modules), and the modules that follow Start are told.
@router.get("/api/internal/captions/outside")
async def captions_outside(request: Request):
    _authorised(request)
    from . import streamstatus
    from .modules import modules as registry
    from .scheduler import scheduler

    return {"pills": registry.pills(), "module_health": registry.health_states(), "module_status": registry.status(),
            "schedule": scheduler.status(), "streams": streamstatus.streams.entries(admin=True)}


@router.post("/api/internal/stream/announce")
async def stream_announce(request: Request):
    """`stream.started` or `stream.stopped`: the Rust server opened or closed the stream, and each module that follows it hears."""
    _authorised(request)
    from . import modules, stream as stream_mod

    body = await request.json()
    event = str(body.get("event") or "")
    if event not in (stream_mod.STARTED, stream_mod.STOPPED):
        raise HTTPException(400, "unknown event")
    modules.announce(event)
    return {"ok": True}


@router.get("/api/internal/routes/providers")
async def route_providers(request: Request):
    """Each module that is on and applies routes: whether its destinations take one source or mix, whether the gear has a gain,
    and the permission that may route them. `gear` lists the modules that can rename a channel on their gear."""
    _authorised(request)
    from .modules import modules as registry

    providers = {pid: {"exclusive": bool(api.get("exclusive")), "gain": bool(api.get("gain")),
                       "permission": api["permission"] if isinstance(api.get("permission"), str) else None}
                 for pid, api in _route_apis().items()}
    gear = [pid for pid, api in registry.using_all("gear.").items()
            if isinstance(api, dict) and callable(api.get("rename")) and callable(api.get("can_rename"))]
    return {"providers": providers, "gear": gear}


@router.post("/api/internal/routes/apply")
async def route_apply(request: Request):
    _authorised(request)
    import asyncio

    body = await request.json()
    api = _route_apis().get(str(body.get("provider")))
    if api is None:
        return {"problem": "That module is not on."}
    try:
        said = await asyncio.to_thread(api["apply"], body["end"], body.get("wanted") or [])
    except Exception:  # the gear's module failed; the route stays asked for
        import logging

        logging.getLogger("relay.routing").exception("routing: %s could not apply %s", body.get("provider"), (body.get("end") or {}).get("key"))
        said = "The gear did not take that."
    return {"problem": str(said) if said else ""}


@router.post("/api/internal/routes/read")
async def route_read(request: Request):
    _authorised(request)
    import asyncio

    body = await request.json()
    api = _route_apis().get(str(body.get("provider")))
    read = api.get("read") if api else None
    if not callable(read):
        return {"read": None}
    try:
        got = await asyncio.to_thread(read, body["end"])
    except Exception:  # a module's own read must not break a page
        import logging

        logging.getLogger("relay.routing").exception("routing: %s could not read %s", body.get("provider"), (body.get("end") or {}).get("key"))
        return {"read": None}
    return {"read": None if got is None else [{"provider": i.get("provider"), "key": i.get("key"), "gain_db": i.get("gain_db")} for i in got]}


@router.post("/api/internal/gear/rename")
async def gear_rename(request: Request):
    _authorised(request)
    import asyncio

    from .modules import modules as registry

    body = await request.json()
    api = registry.using(f"gear.{body.get('provider')}")
    if not (isinstance(api, dict) and callable(api.get("rename"))):
        return {"ok": False}
    try:
        await asyncio.to_thread(api["rename"], str(body.get("key")), str(body.get("name")))
    except ValueError:
        return {"ok": False}
    return {"ok": True}


@router.get("/api/internal/front")
async def front_plan(request: Request):
    _authorised(request)
    return plan()


@router.get("/api/internal/front/admin")
async def front_admin(request: Request):
    """The modules that are on and the admin pages they add, for the server to draw the admin page with (#719)."""
    _authorised(request)
    import asyncio

    from .modules import modules

    return await asyncio.to_thread(modules.admin_listing)


@router.post("/api/internal/front")
async def front_report(request: Request):
    _authorised(request)
    try:
        report = await request.json()
        apply(report)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Bad report") from None
    return {"ok": True}
