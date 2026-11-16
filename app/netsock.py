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
import ipaddress
import logging
import socket
import struct
import sys
from typing import Any, Callable

from . import netinfo, netroute
from .netroute import Resolved

log = logging.getLogger("relay.netsock")

# Not every Python build names these.
IP_BOUND_IF = 25          # macOS <netinet/in.h>
IP_UNICAST_IF = 31        # Windows <ws2ipdef.h>
SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)  # Linux <asm-generic/socket.h>

PROC_CONF = "/proc/sys/net/ipv4/conf"


class RouteError(Exception):
    """A routed socket could not be made. The message is a fixed sentence fit for the admin page."""


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
    sock = _new(socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        if _pinned_route(r):
            pin(sock, r)
            sock.bind((r.address, 0))
        sock.connect((host, int(port)))
    except RouteError:
        _close(sock)
        raise
    except OSError:
        _close(sock)
        raise RouteError(f"Could not connect to {host} port {port} through {_name(r)}.") from None
    return sock


async def open_tcp(route: Resolved | str, host: str, port: int, timeout: float = 5.0,
                   **kwargs: Any) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """asyncio streams to host:port through the route's port. `kwargs` go to asyncio.open_connection (ssl, ...)."""
    sock = await asyncio.to_thread(connect_tcp, route, host, port, timeout)
    sock.setblocking(False)
    if kwargs.get("ssl") and "server_hostname" not in kwargs:
        kwargs["server_hostname"] = host
    try:
        return await asyncio.open_connection(sock=sock, **kwargs)
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


# ------------------------------------------------------------------ UDP and multicast

def udp_socket(route: Resolved | str, port: int = 0, *, broadcast: bool = False, reuse: bool = False,
               host: str | None = None) -> socket.socket:
    """A UDP socket bound to the route's address and `port` (0: any), pinned to its interface. An unrouted class
    with port 0 and no `host` is left unbound (a sender: the OS binds it on the first send, as before); otherwise
    it is bound like listen_tcp, so a receiver passes `host` or a port."""
    r = _resolved(route)
    _require(r)
    sock = _new(socket.SOCK_DGRAM)
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


async def udp_endpoint(route: Resolved | str, protocol_factory: Callable[[], asyncio.DatagramProtocol],
                       port: int = 0, **kwargs: Any) -> tuple[asyncio.DatagramTransport, asyncio.DatagramProtocol]:
    """loop.create_datagram_endpoint on a routed UDP socket. `kwargs` go to udp_socket (broadcast, reuse)."""
    sock = udp_socket(route, port, **kwargs)
    sock.setblocking(False)
    return await asyncio.get_running_loop().create_datagram_endpoint(protocol_factory, sock=sock)


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
