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
    return {"listeners": listeners, "listener_prefixes": list(modules.listener_prefixes()),
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


@router.get("/api/internal/front")
async def front_plan(request: Request):
    _authorised(request)
    return plan()


@router.post("/api/internal/front")
async def front_report(request: Request):
    _authorised(request)
    try:
        report = await request.json()
        apply(report)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Bad report") from None
    return {"ok": True}
