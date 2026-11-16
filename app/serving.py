"""Which ports Relay really serves on, and the sockets and server that serve them.

Viewers get plain HTTP on `port` (80 by default); the operator panel is only ever served over HTTPS on
`admin_port` (443 by default). Relay terminates TLS itself, on the panel socket, so nothing outside this
process is installed or supervised. If 80 or 443 cannot be opened (in use, or not permitted: ports below
1024 need privileges on Linux and macOS), Relay falls back to 8080 / 8443 and says so on the admin page.

`run.py` opens the sockets and publishes the ports that were really bound here; the app reads them back, so
every viewer link, the mDNS record and the viewer-port refusal of admin routes use the port in force, not
the one asked for. Until something is published (a test client, a script importing the app) the app does not
gate by port at all.

Which addresses each listener binds follows its traffic class's route (app/netroute.py, #346): a class with no
route binds the configured host, as before; a routed class binds its port's address and 127.0.0.1 (so the desktop
app and this computer always reach it), so a request to any other address is refused at the socket. While Relay
runs, `rebind()` moves a listener when its port's address changes or the port goes and comes back.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import socket
import sys
from dataclasses import dataclass, field

log = logging.getLogger("relay.serving")

DEFAULT_VIEWER_PORT = 80
DEFAULT_ADMIN_PORT = 443
# Where a default port goes when it cannot be opened. Only the defaults fall back: a port the operator
# chose in config.json that cannot be opened is an error they should see, not a silent substitution.
FALLBACK = {80: 8080, 443: 8443}
LOOPBACK = "127.0.0.1"

_viewer: int | None = None
_admin: int | None = None
_notices: list[str] = []
# Module listeners (#205): port really bound -> the path prefixes that alone answer on it.
_listeners: dict[int, tuple[str, ...]] = {}


def publish_listeners(listeners: dict[int, tuple[str, ...]]) -> None:
    global _listeners
    _listeners = {int(k): tuple(v) for k, v in listeners.items()}


def listener_prefixes(port: int | None) -> tuple[str, ...] | None:
    """The prefixes served on `port` when it is a module listener, else None."""
    return _listeners.get(port) if port is not None else None


def listener_bound(prefixes: tuple[str, ...]) -> int | None:
    """The port a listener serving `prefixes` was really bound to, or None."""
    for port, pre in _listeners.items():
        if tuple(pre) == tuple(prefixes):
            return port
    return None


def publish(viewer: int, admin: int, notices: list[str] | None = None) -> None:
    global _viewer, _admin, _notices
    _viewer, _admin, _notices = int(viewer), int(admin), list(notices or [])


def reset() -> None:
    """Forget the published ports (tests)."""
    global _viewer, _admin, _notices, _listeners, _groups, _live
    _viewer = _admin = None
    _notices = []
    _listeners = {}
    _groups = []
    _live = None


def published() -> bool:
    return _viewer is not None and _admin is not None


def viewer_port(configured: int = DEFAULT_VIEWER_PORT) -> int:
    return _viewer if _viewer is not None else int(configured)


def admin_port(configured: int = DEFAULT_ADMIN_PORT) -> int:
    return _admin if _admin is not None else int(configured)


def add_notice(text: str) -> None:
    _notices.append(text)


def notices() -> list[str]:
    return list(_notices) + [n for g in _groups for n in g.notes]


# ------------------------------------------------------------------ which addresses each listener binds

@dataclass
class Group:
    """The sockets of one listener (viewers, the panel, a module's port) and the traffic class that places them."""
    key: str                       # "viewers", "panel", "<module id>.<listener name>"
    label: str                     # "viewer pages", "operator panel", ...
    traffic: str | None            # its class (netroute); None for sockets opened without a plan (tests)
    configured: str                # the host it binds when the class has no route (config "host" / "admin_host")
    port: int                      # the port really bound
    secure: bool = False
    socks: dict[str, socket.socket] = field(default_factory=dict)        # host -> socket, until served
    servers: dict[str, asyncio.base_events.Server] = field(default_factory=dict)  # host -> serving it
    notes: list[str] = field(default_factory=list)

    @property
    def hosts(self) -> list[str]:
        return list(self.servers or self.socks)


_groups: list[Group] = []


def groups() -> list[Group]:
    return list(_groups)


def plan(traffic: str | None, configured: str, port: int = 0, secure: bool = False) -> tuple[list[str], list[str]]:
    """(hosts to bind, notices) for a listener of `traffic`.

    No route, or a route that fell back to the default network: the configured host, as before routes existed.
    A route whose port is there: that port's address and 127.0.0.1. A strict route whose port is missing: only
    127.0.0.1, so this computer still reaches it and nothing else does."""
    if traffic is None:
        return [configured], []
    from . import netroute

    try:
        r = netroute.resolve(traffic)
    except KeyError:
        return [configured], []
    if not r.routed or r.fallback:
        return [configured], []
    notes: list[str] = []
    if r.available and r.address:
        hosts = [r.address, LOOPBACK] if r.address != LOOPBACK else [LOOPBACK]
    else:
        hosts = [LOOPBACK]
        notes.append(f"{r.reason} Until it is back, only this computer can reach it.")
    if traffic == "crew" and secure:
        where = f"{r.address} and " if r.available and r.address else ""
        notes.append(f"The operator panel answers only on {where}this computer ({link('https', LOOPBACK, port, '/admin')}). "
                     "If you are locked out, open it on this computer, or remove \"crew\" from \"routes\" under "
                     "\"network\" in config.json and restart Relay.")
    return hosts, notes


def listen_group(key: str, label: str, traffic: str | None, configured: str, port: int,
                 secure: bool = False) -> Group:
    """Open every socket of one listener, falling back from 80/443 together. Raises SystemExit like listen()."""
    hosts, notes = plan(traffic, configured, port, secure)
    socks: dict[str, socket.socket] = {}
    bound = port
    try:
        for i, host in enumerate(hosts):
            if i == 0:
                sock, bound, note = listen(host, port, label)
                if note:
                    notes.append(note)
            else:
                try:
                    sock = _bind(host, bound)
                except OSError as exc:
                    notes.append(f"The {label} could not also listen on {host}:{bound} ({_why(exc)}).")
                    continue
            socks[host] = sock
    except SystemExit:
        for sock in socks.values():
            sock.close()
        raise
    return Group(key, label, traffic, configured, bound, secure, socks, notes=[n for n in notes if n])


def publish_groups(found: list[Group]) -> None:
    global _groups
    _groups = list(found)


class _Live:
    """The running server's way to serve a new socket, kept for rebind()."""

    def __init__(self, server, serve, loop=None):
        self.server, self.serve, self.loop = server, serve, loop


_live: _Live | None = None


def rebind_soon() -> None:
    """rebind() on the running server's loop, from any thread (the Network page after routes change)."""
    if _live is not None and _live.loop is not None:
        asyncio.run_coroutine_threadsafe(rebind(), _live.loop)


async def rebind() -> list[str]:
    """Move each listener whose class now resolves to other addresses. Returns the keys of those moved.

    New sockets are opened after the old ones close, because Linux will not bind a port's address while the
    same port is bound to every address. If a new address cannot be opened the listener keeps 127.0.0.1."""
    if _live is None:
        return []
    moved: list[str] = []
    for g in _groups:
        if g.traffic is None:
            continue
        want, notes = plan(g.traffic, g.configured, g.port, g.secure)
        g.notes = notes
        have = list(g.servers)
        if set(want) == set(have):
            continue
        for host in [h for h in have if h not in want]:
            srv = g.servers.pop(host)
            srv.close()
            if srv in _live.server.servers:
                _live.server.servers.remove(srv)
        for host in [h for h in want if h not in g.servers] + ([LOOPBACK] if not want else []):
            try:
                sock = _bind(host, g.port)
            except OSError as exc:
                g.notes.append(f"The {g.label} could not listen on {host}:{g.port} ({_why(exc)}). Restart Relay "
                               "if it does not recover.")
                continue
            srv = await _live.serve(sock, g.secure)
            _live.server.servers.append(srv)
            g.servers[host] = srv
        if not g.servers and LOOPBACK not in want:
            try:
                srv = await _live.serve(_bind(LOOPBACK, g.port), g.secure)
                _live.server.servers.append(srv)
                g.servers[LOOPBACK] = srv
            except OSError:
                pass
        log.info("The %s now listens on %s port %d", g.label, ", ".join(g.servers) or "nothing", g.port)
        moved.append(g.key)
    return moved


def link(scheme: str, host: str, port: int, path: str = "/") -> str:
    """`scheme://host[:port]path`, leaving the port out when it is the scheme's own."""
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}{'' if int(port) == default else f':{int(port)}'}{path}"


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # SO_REUSEADDR on Windows lets a second process take a port that is already in use, which would
            # defeat the fallback below; exclusive use makes the bind fail when the port is taken.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(2048)
        sock.set_inheritable(True)
    except OSError:
        sock.close()
        raise
    return sock


def _why(exc: OSError) -> str:
    if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", 0) == 10048:
        return "it is already in use"
    if exc.errno in (errno.EACCES, errno.EPERM) or getattr(exc, "winerror", 0) == 10013:
        return "this account is not permitted to use it"
    # A fixed sentence, never the exception's own text: this reaches the admin page.
    return "it could not be opened"


def listen(host: str, port: int, what: str) -> tuple[socket.socket, int, str | None]:
    """Open a listening socket on `port`, falling back for 80 and 443.

    Returns (socket, port really bound, a sentence for the admin page when it had to fall back).
    Raises SystemExit with a plain sentence when nothing can be opened.
    """
    try:
        sock = _bind(host, port)
        return sock, sock.getsockname()[1], None
    except OSError as exc:
        alt = FALLBACK.get(port)
        if alt is None:
            raise SystemExit(f"Cannot listen on {host}:{port} for the {what}: {_why(exc)}.") from exc
        try:
            sock = _bind(host, alt)
        except OSError as exc2:
            raise SystemExit(f"Cannot listen on {host}:{port} for the {what} ({_why(exc)}) or on "
                             f"{alt} ({_why(exc2)}).") from exc2
        return sock, alt, f"The {what} could not use port {port} ({_why(exc)}), so it is on port {alt}."


def make_server(app: str, plain: list[socket.socket], secure: list[socket.socket], context, on_ready=None,
                groups: list[Group] | None = None):
    """One uvicorn server: plain sockets speak HTTP, secure sockets speak HTTPS with `context`.

    With `groups` (run.py), each group's sockets are served and the server is kept so rebind() can move them;
    `plain` and `secure` are then ignored. Without, the two lists are served as they are (tests).
    uvicorn applies one TLS setting to every socket it is given, so every socket is added here once uvicorn has
    started. Still one process and one event loop, which the engine and hub need. `on_ready()` is called once
    every socket is accepting (the desktop shell waits for it).
    """
    import uvicorn

    if groups is None:
        groups = [Group("plain", "plain", None, "", 0, False, {str(i): s for i, s in enumerate(plain)}),
                  Group("secure", "secure", None, "", 0, True, {str(i): s for i, s in enumerate(secure)})]

    class RelayServer(uvicorn.Server):
        async def startup(self, sockets=None):  # noqa: D401 - uvicorn's own hook
            await super().startup(sockets=[])
            if not self.started:  # the app's own start-up failed
                return
            config = self.config

            def create_protocol(_loop=None):
                return config.http_protocol_class(config=config, server_state=self.server_state,
                                                  app_state=self.lifespan.state, _loop=_loop)

            loop = asyncio.get_running_loop()

            async def serve(sock, is_secure):
                return await loop.create_server(create_protocol, sock=sock, ssl=context if is_secure else None,
                                                backlog=config.backlog)

            for g in groups:
                for host, sock in list(g.socks.items()):
                    srv = await serve(sock, g.secure)
                    self.servers.append(srv)
                    g.servers[host] = srv
                g.socks.clear()
            global _live
            _live = _Live(self, serve, loop)
            if on_ready is not None:
                on_ready()

    return RelayServer(uvicorn.Config(app, log_level="info", access_log=False))
