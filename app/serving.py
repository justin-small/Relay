"""Which ports Relay really serves on, and the sockets and server that serve them.

Viewers get plain HTTP on `port` (80 by default); the operator panel is only ever served over HTTPS on
`admin_port` (443 by default). Relay terminates TLS itself, on the panel socket, so nothing outside this
process is installed or supervised. If 80 or 443 cannot be opened (in use, or not permitted: ports below
1024 need privileges on Linux and macOS), Relay falls back to 8080 / 8443 and says so on the admin page.

`run.py` opens the sockets and publishes the ports that were really bound here; the app reads them back, so
every viewer link, the mDNS record and the viewer-port refusal of admin routes use the port in force, not
the one asked for. Until something is published (a test client, a script importing the app) the app does not
gate by port at all.
"""
from __future__ import annotations

import asyncio
import errno
import socket
import sys

DEFAULT_VIEWER_PORT = 80
DEFAULT_ADMIN_PORT = 443
# Where a default port goes when it cannot be opened. Only the defaults fall back: a port the operator
# chose in config.json that cannot be opened is an error they should see, not a silent substitution.
FALLBACK = {80: 8080, 443: 8443}

_viewer: int | None = None
_admin: int | None = None
_notices: list[str] = []


def publish(viewer: int, admin: int, notices: list[str] | None = None) -> None:
    global _viewer, _admin, _notices
    _viewer, _admin, _notices = int(viewer), int(admin), list(notices or [])


def reset() -> None:
    """Forget the published ports (tests)."""
    global _viewer, _admin, _notices
    _viewer = _admin = None
    _notices = []


def published() -> bool:
    return _viewer is not None and _admin is not None


def viewer_port(configured: int = DEFAULT_VIEWER_PORT) -> int:
    return _viewer if _viewer is not None else int(configured)


def admin_port(configured: int = DEFAULT_ADMIN_PORT) -> int:
    return _admin if _admin is not None else int(configured)


def notices() -> list[str]:
    return list(_notices)


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


def make_server(app: str, plain: list[socket.socket], secure: list[socket.socket], context):
    """One uvicorn server: `plain` sockets speak HTTP, `secure` sockets speak HTTPS with `context`.

    uvicorn applies one TLS setting to every socket it is given, so the HTTPS sockets are added here once it
    has started the plain ones. Still one process and one event loop, which the engine and hub need.
    """
    import uvicorn

    class RelayServer(uvicorn.Server):
        async def startup(self, sockets=None):  # noqa: D401 - uvicorn's own hook
            await super().startup(sockets=plain)
            if not self.started:  # the app's own start-up failed
                return
            config = self.config

            def create_protocol(_loop=None):
                return config.http_protocol_class(config=config, server_state=self.server_state,
                                                  app_state=self.lifespan.state, _loop=_loop)

            loop = asyncio.get_running_loop()
            for sock in secure:
                self.servers.append(await loop.create_server(
                    create_protocol, sock=sock, ssl=context, backlog=config.backlog))

    return RelayServer(uvicorn.Config(app, log_level="info", access_log=False))
