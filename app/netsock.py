"""Sockets that really leave through the network port a traffic class is routed to (#345).

Binding a socket to a port's address picks only the source address: the route table still picks the interface
by destination, so traffic for another network can leave through the default port anyway. A routed socket is
therefore also pinned to the interface:

* Linux: SO_BINDTODEVICE. Kernels before 5.7 refuse it without CAP_NET_RAW; the socket then keeps the source
  address only, which is right whenever the device is on that port's own network, and `notes()` says so.
* macOS: IP_BOUND_IF, with the interface index.
* Windows: IP_UNICAST_IF, with the index in network byte order. Windows' weak host model can still send
  replies out another port (docs/modules.md).
* Multicast: IP_MULTICAST_IF to the port's address on the send side, and the group joined on that address,
  never on 0.0.0.0 (which joins on whichever port the OS likes).

A class with no route, or one that fell back to the default network, gets an ordinary socket: exactly what Relay
did before routes existed. Every helper takes a class id or a `netroute.Resolved`, and fails with `RouteError`,
whose text is a fixed sentence (never the OS's error text, since it reaches the admin page).
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

import websockets

from . import netinfo, netroute
from .netroute import Resolved

log = logging.getLogger("relay.netsock")

# Not every Python build names these.
IP_BOUND_IF = 25          # macOS <netinet/in.h>
IP_UNICAST_IF = 31        # Windows <ws2ipdef.h>
SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)  # Linux <asm-generic/socket.h>

PROC_CONF = "/proc/sys/net/ipv4/conf"


class RouteError(OSError):
    """A routed socket could not be made. The message is a fixed sentence fit for the admin page.

    An OSError, so a client that already catches OSError around a connection (most do) reports it as one."""


# Ports whose interface pin was refused (Linux without CAP_NET_RAW), by port id: they keep the source address only.
_pin_refused: set[str] = set()


def _resolved(route: Resolved | str) -> Resolved:
    return route if isinstance(route, Resolved) else netroute.resolve(route)


def _pinned_route(r: Resolved) -> bool:
    """Whether this resolution needs a pinned socket: routed to a port that is there, not fallen back."""
    return r.routed and not r.fallback and r.port is not None and bool(r.address)


def _require(r: Resolved) -> None:
    if not r.available:
        raise RouteError(r.reason or "The chosen network port cannot be used right now.")


def _name(r: Resolved) -> str:
    return (r.port.friendly_name or r.port.name) if r.port else "the chosen network port"


def _index(port: netinfo.Port) -> int | None:
    if port.index:
        return port.index
    try:
        return socket.if_nametoindex(port.name)
    except (OSError, AttributeError):
        return None


def pin(sock: socket.socket, route: Resolved | str) -> bool:
    """Pin `sock` to the route's interface. True when pinned; False for an unrouted class or a refused pin.

    Call before connect() or bind() (Linux checks the device on bind). Raises RouteError when the OS refuses
    the option for another reason than missing privilege."""
    r = _resolved(route)
    if not _pinned_route(r):
        return False
    port = r.port
    assert port is not None
    try:
        if sys.platform.startswith("linux"):
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, port.name.encode() + b"\0")
        elif sys.platform == "darwin":
            index = _index(port)
            if not index:
                raise RouteError(f"Relay could not find {_name(r)} to send through.")
            sock.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, index)
        elif sys.platform == "win32":
            index = _index(port)
            if not index:
                raise RouteError(f"Relay could not find {_name(r)} to send through.")
            sock.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, struct.pack("!I", index))
        else:
            return False
    except RouteError:
        raise
    except PermissionError:
        if port.id not in _pin_refused:
            _pin_refused.add(port.id)
            log.warning("Could not pin traffic to %s without CAP_NET_RAW; sending from its address only", port.name)
        return False
    except OSError as exc:
        log.warning("Could not pin a socket to %s: %s", port.name, exc)
        raise RouteError(f"Relay could not send through {_name(r)}.") from None
    _pin_refused.discard(port.id)
    return True


def _new(kind: int) -> socket.socket:
    return socket.socket(socket.AF_INET, kind)


def _close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


# ------------------------------------------------------------------ TCP

def connect_tcp(route: Resolved | str, host: str, port: int, timeout: float = 5.0) -> socket.socket:
    """A TCP socket connected to host:port through the route's port (blocking, with a timeout)."""
    r = _resolved(route)
    _require(r)
    if not _pinned_route(r):
        # Unrouted or fallen back: an ordinary connection, as before routes existed (IPv6 and all).
        try:
            return socket.create_connection((host, int(port)), timeout)
        except socket.timeout:
            raise RouteError(f"{host} port {port} did not answer in time.") from None
        except OSError:
            raise RouteError(f"Could not connect to {host} port {port}.") from None
    sock = _new(socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        pin(sock, r)
        sock.bind((r.address, 0))
        sock.connect((host, int(port)))
    except RouteError:
        _close(sock)
        raise
    except socket.timeout:
        _close(sock)
        raise RouteError(f"{host} port {port} did not answer in time through {_name(r)}.") from None
    except OSError:
        _close(sock)
        raise RouteError(f"Could not connect to {host} port {port} through {_name(r)}.") from None
    return sock


async def open_tcp(route: Resolved | str, host: str, port: int, timeout: float = 5.0,
                   **kwargs: Any) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """asyncio streams to host:port through the route's port. `kwargs` go to asyncio.open_connection (ssl, ...)."""
    if kwargs.get("ssl") is True:
        kwargs["ssl"] = tls_context()
    sock = await asyncio.to_thread(connect_tcp, route, host, port, timeout)
    sock.setblocking(False)
    if kwargs.get("ssl") and "server_hostname" not in kwargs:
        kwargs["server_hostname"] = host
    try:
        return await asyncio.open_connection(sock=sock, **kwargs)
    except ssl.SSLError:
        _close(sock)
        raise RouteError(f"The TLS connection to {host} port {port} failed. Check its certificate.") from None
    except OSError:
        _close(sock)
        raise RouteError(f"Could not connect to {host} port {port} through {_name(_resolved(route))}.") from None


def _unrouted_host(r: Resolved, host: str | None) -> str:
    """Where an unrouted class listens: the `host` the caller passes (a setting such as config "host"), else the
    default network's address. Never a literal "every interface": CodeQL refuses those (a learning, #237)."""
    return host if host is not None else (r.address or "127.0.0.1")


def listen_tcp(route: Resolved | str, port: int, backlog: int = 100, *, host: str | None = None) -> socket.socket:
    """A listening TCP socket on the route's address. An unrouted class listens on `host`, else on the
    default network's address."""
    r = _resolved(route)
    _require(r)
    sock = _new(socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":  # on Windows SO_REUSEADDR lets another process take the port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if _pinned_route(r):
            pin(sock, r)
            sock.bind((r.address, int(port)))
        else:
            sock.bind((_unrouted_host(r, host), int(port)))
        sock.listen(backlog)
    except RouteError:
        _close(sock)
        raise
    except OSError:
        _close(sock)
        where = _name(r) if _pinned_route(r) else "this computer"
        raise RouteError(f"Port {port} on {where} is already in use or cannot be opened.") from None
    return sock


async def start_server(route: Resolved | str, client_connected: Callable[..., Any], port: int, *,
                       host: str | None = None, **kwargs: Any) -> asyncio.base_events.Server:
    """asyncio.start_server on the route's address (see listen_tcp for `host`)."""
    sock = listen_tcp(route, port, host=host)
    sock.setblocking(False)
    return await asyncio.start_server(client_connected, sock=sock, **kwargs)


# ------------------------------------------------------------------ HTTP and WebSocket clients (#348)

def failure(route: Resolved | str, default: str) -> str:
    """What to tell the operator when a connection through `route` failed: the route's own reason when its port
    cannot be used, `default` plus the port it went through when routed, else `default`. Built from the route,
    never from an exception's text, so it is safe for any page."""
    try:
        r = _resolved(route)
    except KeyError:
        return default
    if not r.available:
        return r.reason or default
    if _pinned_route(r):
        return f"{default.rstrip('.')}. It goes out through {_name(r)} ({r.address})."
    if r.fallback and r.reason:
        return f"{default.rstrip('.')}. {r.reason}"
    return default


def _timeout(value: Any, default: float = 30.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _creator(route: Resolved | str) -> Callable[..., socket.socket]:
    """A stand-in for socket.create_connection that connects through the route (http.client calls it)."""
    def create(address: tuple, timeout: Any = None, source_address: Any = None) -> socket.socket:
        return connect_tcp(route, address[0], address[1], _timeout(timeout))
    return create


_tls: ssl.SSLContext | None = None


def tls_context() -> ssl.SSLContext:
    """The context Relay verifies servers with when a caller passes none: the system's roots plus certifi's.

    Without certifi, a Python with no root store of its own (python.org's macOS build before its Install
    Certificates script runs, some minimal Linux images) fails every HTTPS request with CERTIFICATE_VERIFY_FAILED,
    and YouTube, Google sign-in and Relay's cloud all read as "could not reach". Verification stays on."""
    global _tls
    if _tls is None:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except (ImportError, OSError):
            log.warning("certifi is missing; HTTPS uses only this computer's own certificate store")
        _tls = ctx
    return _tls


def http_connection(route: Resolved | str, host: str, port: int | None = None, *, tls: bool = False,
                    timeout: float = 30.0, **kwargs: Any) -> http.client.HTTPConnection:
    """An http.client connection (HTTPS when `tls`; `kwargs` such as context go to it) whose socket is opened
    through the route when it connects. The class is resolved again on every connect."""
    cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
    if tls and kwargs.get("context") is None:
        kwargs["context"] = tls_context()
    conn = cls(host, port, timeout=timeout, **kwargs)
    conn._create_connection = _creator(route)  # type: ignore[attr-defined]  # http.client's own hook
    return conn


def _routed_class(route: Resolved | str, base: Callable[..., http.client.HTTPConnection]):
    def make(host: str, **kwargs: Any) -> http.client.HTTPConnection:
        conn = base(host, **kwargs)
        conn._create_connection = _creator(route)  # type: ignore[attr-defined]
        return conn
    return make


class _RoutedHTTP(urllib.request.HTTPHandler):
    def __init__(self, route: Resolved | str):
        super().__init__()
        self._route = route

    def do_open(self, http_class, req, **kwargs):  # type: ignore[override]
        return super().do_open(_routed_class(self._route, http_class), req, **kwargs)


class _RoutedHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, route: Resolved | str, context: Any = None):
        super().__init__(context=context)
        self._route = route

    def do_open(self, http_class, req, **kwargs):  # type: ignore[override]
        return super().do_open(_routed_class(self._route, http_class), req, **kwargs)


def opener(route: Resolved | str, *handlers: Any, context: Any = None) -> urllib.request.OpenerDirector:
    """A urllib opener whose http and https requests go out through the route. `handlers` are added as with
    urllib.request.build_opener (a redirect refuser, a ProxyHandler); pass an SSL context as `context`, never an
    HTTPSHandler of your own (it would replace the routed one). With no context, tls_context() is used."""
    if any(isinstance(h, (urllib.request.HTTPHandler, urllib.request.HTTPSHandler)) or
           h in (urllib.request.HTTPHandler, urllib.request.HTTPSHandler) for h in handlers):
        raise ValueError("pass context= to netsock.opener, not an HTTP or HTTPS handler")
    return urllib.request.build_opener(_RoutedHTTP(route), _RoutedHTTPS(route, context or tls_context()), *handlers)


def urlopen(route: Resolved | str, request: urllib.request.Request | str, *, timeout: float = 30.0,
            context: Any = None):
    """urllib.request.urlopen through the route."""
    return opener(route, context=context).open(request, timeout=timeout)


def fetch(route: Resolved | str, url: str, *, form: dict | None = None, headers: dict | None = None,
          timeout: float = 15.0, limit: int = 1_000_000, json_body: Any = None,
          method: str | None = None) -> tuple[int, bytes]:
    """One HTTP request through the route: a POST of `form` (url-encoded) or of `json_body` when given, else a GET;
    `method` overrides the verb (PUT, DELETE). Returns the status and at most `limit` bytes of the body, for an error
    status too (an OAuth server answers 400 with JSON). A connection that fails raises OSError (RouteError when the
    route is the cause). Blocking: run it in a thread."""
    if json_body is not None:
        data: bytes | None = json.dumps(json_body).encode("utf-8")
        kind = "application/json"
    else:
        data = urllib.parse.urlencode(form).encode("ascii") if form is not None else None
        kind = "application/x-www-form-urlencoded"
    verb = method or ("POST" if data is not None else "GET")
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=verb)
    if data is not None:
        req.add_header("Content-Type", kind)
    req.add_header("Accept", "application/json")
    try:
        with urlopen(route, req, timeout=timeout) as resp:
            return resp.status, resp.read(limit)
    except urllib.error.HTTPError as e:
        with contextlib.closing(e):
            return e.code, e.read(limit)


async def websocket(route: Resolved | str, url: str, *, open_timeout: float = 10.0, **kwargs: Any):
    """`await websockets.connect(url, ...)` through the route: the TCP connection is opened by connect_tcp and
    handed to websockets, which does TLS (wss) and the handshake. Returns the open connection; close it, or use
    websocket_session."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ValueError("a ws:// or wss:// address is needed")
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    if parts.scheme == "wss" and kwargs.get("ssl") in (None, True):
        kwargs["ssl"] = tls_context()
    sock = await asyncio.to_thread(connect_tcp, route, parts.hostname, port, open_timeout)
    sock.setblocking(False)
    try:
        return await websockets.connect(url, sock=sock, open_timeout=open_timeout, **kwargs)
    except BaseException:
        _close(sock)
        raise


@contextlib.asynccontextmanager
async def websocket_session(route: Resolved | str, url: str, **kwargs: Any):
    """`async with netsock.websocket_session(...) as ws`: websocket(), closed on the way out."""
    ws = await websocket(route, url, **kwargs)
    try:
        yield ws
    finally:
        await ws.close()


# ------------------------------------------------------------------ UDP and multicast

def udp_socket(route: Resolved | str, port: int = 0, *, broadcast: bool = False, reuse: bool = False,
               host: str | None = None) -> socket.socket:
    """A UDP socket bound to the route's address and `port` (0: any), pinned to its interface. An unrouted class
    with port 0 and no `host` is left unbound (a sender: the OS binds it on the first send, as before); otherwise
    it is bound like listen_tcp, so a receiver passes `host` or a port."""
    r = _resolved(route)
    _require(r)
    sock = _new(socket.SOCK_DGRAM)
    no_connreset(sock)
    try:
        if reuse:
            _reuse(sock)
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if _pinned_route(r):
            pin(sock, r)
            sock.bind((r.address, int(port)))
        elif port or host is not None:
            sock.bind((_unrouted_host(r, host), int(port)))
    except RouteError:
        _close(sock)
        raise
    except OSError:
        _close(sock)
        raise RouteError(f"UDP port {port} on {_name(r)} is already in use or cannot be opened.") from None
    return sock


def no_connreset(sock: socket.socket) -> None:
    """Windows: stop an ICMP "port unreachable" (a peer that closed its socket) from failing the next receive on a
    UDP socket (SIO_UDP_CONNRESET). Nothing to do elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        off, got = ctypes.c_ulong(0), ctypes.c_ulong(0)
        ctypes.windll.ws2_32.WSAIoctl(ctypes.c_size_t(sock.fileno()), 0x9800000C, ctypes.byref(off), 4, None, 0,
                                      ctypes.byref(got), None, None)
    except Exception:
        pass


def _reuse(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32":
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass


def _group(group: str) -> str:
    try:
        addr = ipaddress.IPv4Address(group)
    except ValueError:
        raise ValueError(f"{group!r} is not an IPv4 multicast group") from None
    if not addr.is_multicast:
        raise ValueError(f"{group!r} is not an IPv4 multicast group")
    return str(addr)


def join_multicast(sock: socket.socket, group: str, route: Resolved | str, *, ttl: int | None = None,
                   loop: bool | None = None) -> None:
    """Send `sock`'s multicast out of the route's port and join `group` on that port's address."""
    r = _resolved(route)
    _require(r)
    group = _group(group)
    local = r.address if _pinned_route(r) and r.address else "0.0.0.0"  # nosec B104 - unrouted: as before
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton(local))
        if ttl is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, max(1, min(255, int(ttl))))
        if loop is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if loop else 0)
    except OSError:
        raise RouteError(f"Could not join multicast group {group} on {_name(r)}.") from None


def multicast_socket(route: Resolved | str, group: str, port: int, *, ttl: int | None = None,
                     loop: bool | None = None) -> socket.socket:
    """A UDP socket that receives `group`:`port` on the route's port only, and sends to it from there.

    It binds the group: a socket bound to the port's unicast address would never see the group's packets on Linux
    or macOS. Windows cannot bind a group; there a socket bound to the interface's own address receives what was
    joined on it."""
    r = _resolved(route)
    _require(r)
    group = _group(group)
    sock = _new(socket.SOCK_DGRAM)
    try:
        _reuse(sock)
        if _pinned_route(r):
            pin(sock, r)
        sock.bind(((r.address or "127.0.0.1") if sys.platform == "win32" else group, int(port)))
        join_multicast(sock, group, r, ttl=ttl, loop=loop)
    except RouteError:
        _close(sock)
        raise
    except OSError:
        _close(sock)
        raise RouteError(f"UDP port {port} for {group} on {_name(r)} is already in use or cannot be opened.") from None
    return sock


def multicast_sender(route: Resolved | str, *, ttl: int | None = None, port: int = 0) -> socket.socket:
    """A UDP socket that sends multicast (an mDNS question) out of the route's port and hears unicast answers on its
    own port. It joins no group; use multicast_socket to receive a group."""
    r = _resolved(route)
    sock = udp_socket(r, port)
    try:
        if _pinned_route(r) and r.address:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(r.address))
        if ttl is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, max(1, min(255, int(ttl))))
    except OSError:
        _close(sock)
        raise RouteError(f"Could not send multicast through {_name(r)}.") from None
    return sock


def _source_toward(peer: tuple[str, int] | None) -> str | None:
    """The local address the OS would send from to reach `peer` (a throwaway connected UDP socket sends nothing),
    or None when it cannot tell. Lets an unbound sender bind where the peer's replies will come back to."""
    if peer is None:
        return None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer[0], int(peer[1])))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


async def udp_endpoint(route: Resolved | str, protocol_factory: Callable[[], asyncio.DatagramProtocol],
                       port: int = 0, *, remote: tuple[str, int] | None = None, toward: tuple[str, int] | None = None,
                       **kwargs: Any) -> tuple[asyncio.DatagramTransport, asyncio.DatagramProtocol]:
    """loop.create_datagram_endpoint on a routed UDP socket. `remote` (host, port) connects it, so the transport's
    sendto() needs no address, as with create_datagram_endpoint(remote_addr=...). `toward` names the peer an unconnected
    sender will talk to, so on Windows (where it must be bound to receive) it binds on the address that reaches it. `kwargs` go to udp_socket
    (broadcast, reuse)."""
    loop = asyncio.get_running_loop()
    target = None
    if remote is not None:
        try:
            infos = await loop.getaddrinfo(remote[0], int(remote[1]), family=socket.AF_INET, type=socket.SOCK_DGRAM)
        except OSError:
            raise RouteError(f"Could not find {remote[0]}.") from None
        target = infos[0][4]
    sock = udp_socket(route, port, **kwargs)
    try:
        if target is not None:
            sock.connect(target)
        elif sys.platform == "win32":
            # A sender left unbound is bound by the OS on its first send, but asyncio starts receiving at once and
            # an unbound socket cannot receive on Windows (WinError 10022): replies never arrive. Bind it to a free
            # port on the default network's address now (never every interface, CodeQL); getsockname() raises on an unbound socket there.
            try:
                sock.getsockname()
            except OSError:
                sock.bind((_source_toward(toward) or _unrouted_host(_resolved(route), None), 0))
        sock.setblocking(False)
        transport, protocol = await loop.create_datagram_endpoint(protocol_factory, sock=sock)
        if target is not None and getattr(transport, "_address", 0) is None:
            # Windows' proactor transport does not learn the peer of a socket it is handed, so sendto(data) with no
            # address passes None to WSASendTo and fails. Telling it the peer makes it send on the connected socket.
            transport._address = target
        return transport, protocol
    except OSError:
        _close(sock)
        raise RouteError(f"Could not send to {remote[0] if remote else 'that address'} through "
                         f"{_name(_resolved(route))}.") from None


# ------------------------------------------------------------------ what the operator should know

def _rp_filter(name: str) -> int | None:
    """Linux reverse-path filtering for an interface: the larger of its own and "all" (the kernel's rule)."""
    values = []
    for which in ("all", name):
        try:
            with open(f"{PROC_CONF}/{which}/rp_filter", encoding="ascii") as fh:
                values.append(int(fh.read().strip()))
        except (OSError, ValueError):
            pass
    return max(values) if values else None


def overlapping(ports: list[netinfo.Port]) -> list[tuple[netinfo.Port, netinfo.Port, str]]:
    """Pairs of ports with addresses on the same IPv4 network: no option here can steer between them."""
    nets: list[tuple[netinfo.Port, ipaddress.IPv4Network]] = []
    for p in ports:
        for a in p.ipv4:
            net = ipaddress.IPv4Interface(a).network
            if not net.is_loopback and net.prefixlen < 32:
                nets.append((p, net))
    out: list[tuple[netinfo.Port, netinfo.Port, str]] = []
    for i, (a, na) in enumerate(nets):
        for b, nb in nets[i + 1:]:
            if a.id != b.id and na.overlaps(nb) and not any(x[:2] == (a, b) for x in out):
                out.append((a, b, str(na if na.prefixlen <= nb.prefixlen else nb)))
    return out


def notes(route: Resolved | str) -> list[str]:
    """Sentences about how well a routed class can be steered: a refused pin, reverse-path filtering, a shared subnet."""
    r = _resolved(route)
    if not _pinned_route(r):
        return []
    port = r.port
    assert port is not None
    out = []
    if port.id in _pin_refused:
        out.append(f"Relay sends {r.traffic} from {_name(r)}'s address but cannot pin it to that port: it needs the "
                   "CAP_NET_RAW capability (AmbientCapabilities=CAP_NET_RAW in its systemd unit). Devices on that "
                   "port's own network are reached correctly; others may be reached through another port.")
    if sys.platform.startswith("linux") and _rp_filter(port.name) == 1:
        out.append(f"Strict reverse-path filtering on {port.name} can drop replies that arrive on another port. "
                   f"Set net.ipv4.conf.{port.name}.rp_filter=2 if replies go missing.")
    for a, b, net in overlapping(netroute.ports()):
        if port.id in (a.id, b.id):
            other = b if a.id == port.id else a
            out.append(f"{_name(r)} and {other.friendly_name or other.name} are both on {net}: this computer "
                       "cannot reliably choose between them. Put them on different networks.")
    return out


# ------------------------------------------------------------------ the Network page's Test button (#347)

def probe(route: Resolved | str) -> dict:
    """Where a class's traffic would leave from, without sending anything: a pinned UDP socket connect()ed to an
    address on the port's own network (or a public one for an unrouted class) makes the OS choose its route and
    source address, which getsockname() then reports."""
    r = _resolved(route)
    if not r.available:
        return {"ok": False, "detail": r.reason or "The chosen network port cannot be used right now.",
                "source": None, "pinned": False, "notes": []}
    if _pinned_route(r) and r.port is not None and r.port.ipv4:
        net = ipaddress.IPv4Interface(r.port.ipv4[0]).network
        hosts = [h for h in (net.network_address + 1, net.network_address + 2) if str(h) != r.address]
        target = str(hosts[0]) if net.num_addresses > 2 and hosts else r.address
    else:
        target = "192.0.2.1"   # TEST-NET-1: any routed destination makes the OS pick the default route
    try:
        sock = udp_socket(r)
    except RouteError:
        return {"ok": False, "detail": f"Relay could not open a socket on {_name(r)} at {r.address}.",
                "source": None, "pinned": False, "notes": []}
    try:
        pinned = (_pinned_route(r) and r.port is not None and r.port.id not in _pin_refused
                  and (sys.platform.startswith("linux") or sys.platform in ("darwin", "win32")))
        sock.connect((target, 9))
        source = sock.getsockname()[0]
    except (OSError, RouteError):
        return {"ok": False, "detail": f"This computer has no route out through {_name(r)}.", "source": None,
                "pinned": False, "notes": notes(r)}
    finally:
        _close(sock)
    expected = r.address if _pinned_route(r) else None
    ok = expected is None or source == expected
    where = _name(r) if _pinned_route(r) else "the system's default network"
    detail = (f"Leaves from {source} on {where}." if ok
              else f"Left from {source}, not {expected}: the OS sent it another way.")
    return {"ok": ok, "detail": detail, "source": source, "pinned": pinned, "notes": notes(r)}
