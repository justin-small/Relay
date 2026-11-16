"""Sockets pinned to a chosen network port (app/netsock.py, #345). Socket options are mocked per platform; a real
send and receive runs over loopback (Linux with extra loopback addresses, which prove the source pin).
Run: .venv/bin/python tests/test_netsock.py"""
import asyncio
import os
import socket
import struct
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(TMP, "certs")
os.environ["RELAY_ANNOUNCE"] = "0"

from app import netroute, netsock  # noqa: E402
from app.netinfo import Port  # noqa: E402
from app.netroute import Resolved  # noqa: E402
from app.netsock import RouteError  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section, tmpdir  # noqa: E402
from _netfake import USB, loopback_route  # noqa: E402
ROUTED = Resolved("dante", USB.id, USB, "10.0.0.5", True, "", "strict")
DEFAULT = Resolved("crew", None, None, "192.168.1.20", True, "", "fallback")
FELL_BACK = Resolved("crew", USB.id, USB, "192.168.1.20", True, "USB LAN is down. Using the default.", "fallback", True)
GONE = Resolved("dante", USB.id, None, None, False, "Dante: USB LAN is not connected to this computer.", "strict")


class FakeSocket:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def _do(self, name, *args):
        self.calls.append((name, *args))
        exc = self.fail.get(name)
        if exc:
            raise exc

    def setsockopt(self, *a): self._do("setsockopt", *a)
    def bind(self, addr): self._do("bind", addr)
    def connect(self, addr): self._do("connect", addr)
    def listen(self, n): self._do("listen", n)
    def settimeout(self, t): self._do("settimeout", t)
    def setblocking(self, b): self._do("setblocking", b)
    def close(self): self._do("close")

    def opts(self):
        return [c[1:] for c in self.calls if c[0] == "setsockopt"]

    def named(self, name):
        return [c[1:] for c in self.calls if c[0] == name]


def fake(platform, fail=None):
    sock = FakeSocket(fail)
    return sock, mock.patch.object(netsock, "_new", lambda kind: sock), mock.patch.object(netsock.sys, "platform", platform)


with section("Linux pins with SO_BINDTODEVICE, then binds the port's address"):
    sock, new, plat = fake("linux")
    with new, plat:
        netsock.connect_tcp(ROUTED, "10.0.0.40", 4440)
    eq("pinned by device name", sock.opts(), [(socket.SOL_SOCKET, netsock.SO_BINDTODEVICE, b"en5\0")])
    eq("source address, then connect", (sock.named("bind"), sock.named("connect")), ([(("10.0.0.5", 0),)], [(("10.0.0.40", 4440),)]))

with section("Linux without CAP_NET_RAW: source address only, and a note says so"):
    netroute.update([USB])
    sock, new, plat = fake("linux", {"setsockopt": PermissionError(1, "Operation not permitted")})
    with new, plat, mock.patch.object(netsock, "_rp_filter", return_value=None):
        netsock.connect_tcp(ROUTED, "10.0.0.40", 4440)
        eq("still bound to the port's address", sock.named("bind"), [(("10.0.0.5", 0),)])
        n = netsock.notes(ROUTED)
    check("the note names the capability", len(n) == 1 and "CAP_NET_RAW" in n[0], n)
    sock, new, plat = fake("linux")
    with new, plat, mock.patch.object(netsock, "_rp_filter", return_value=None):
        netsock.connect_tcp(ROUTED, "10.0.0.40", 4440)
        eq("a pin that works again clears the note", netsock.notes(ROUTED), [])

with section("macOS pins with IP_BOUND_IF and the interface index"):
    sock, new, plat = fake("darwin")
    with new, plat:
        netsock.udp_socket(ROUTED, 5004)
    eq("IP_BOUND_IF 25 with index 7", sock.opts(), [(socket.IPPROTO_IP, 25, 7)])
    eq("bound to the port's address and port", sock.named("bind"), [(("10.0.0.5", 5004),)])
    no_index = Resolved("dante", "name:zz9", Port(id="name:zz9", name="zz9", friendly_name="Gone", ipv4=("10.9.0.1/24",), up=True),
                        "10.9.0.1", True, "", "strict")
    sock, new, plat = fake("darwin")
    with new, plat:
        try:
            netsock.udp_socket(no_index)
            check("an interface with no index is refused", False)
        except RouteError as exc:
            eq("refused in a fixed sentence", str(exc), "Relay could not find Gone to send through.")
    eq("and the socket is closed", sock.named("close"), [()])

with section("Windows pins with IP_UNICAST_IF, index in network byte order"):
    sock, new, plat = fake("win32")
    with new, plat:
        netsock.connect_tcp(ROUTED, "10.0.0.40", 80)
    eq("IP_UNICAST_IF 31 with big-endian 7", sock.opts(), [(socket.IPPROTO_IP, 31, struct.pack("!I", 7))])
    eq("the known trap: not the host-order int", struct.pack("!I", 7), b"\x00\x00\x00\x07")

with section("unrouted and fallen-back classes get ordinary sockets"):
    for label, r in (("no route", DEFAULT), ("fell back to the default", FELL_BACK)):
        sock, new, plat = fake("linux")
        plain = mock.patch.object(netsock.socket, "create_connection", return_value="plain")
        with new, plat, plain as cc:
            got = netsock.connect_tcp(r, "203.0.113.5", 443)
        eq(f"{label}: an ordinary connection (IPv6 too), as before routes", (got, cc.call_args.args),
           ("plain", (("203.0.113.5", 443), 5.0)))
        eq(f"{label}: no pin, no bind", (sock.opts(), sock.named("bind")), ([], []))
    sock, new, plat = fake("linux")
    with new, plat:
        netsock.listen_tcp(DEFAULT, 8080)
    eq("an unrouted listener binds the default network's address", sock.named("bind"), [(("192.168.1.20", 8080),)])
    sock, new, plat = fake("linux")
    with new, plat:
        netsock.udp_socket(DEFAULT, 9910, host="10.1.1.1")
    eq("or the host the caller passes (a setting)", sock.named("bind"), [(("10.1.1.1", 9910),)])
    sock, new, plat = fake("linux")
    with new, plat:
        netsock.udp_socket(DEFAULT)
    eq("an unrouted port-0 UDP socket is left for the OS to bind", sock.named("bind"), [])
    sock, new, plat = fake("win32")
    with new, plat:
        netsock.udp_socket(DEFAULT, 0, host="127.0.0.1")
    eq("an unrouted receiver with a host is bound, so it has a port on Windows", sock.named("bind"), [(("127.0.0.1", 0),)])
    sock, new, plat = fake("freebsd13")
    with new, plat:
        netsock.connect_tcp(ROUTED, "10.0.0.40", 80)
    eq("an OS with no pin option binds the source address only", (sock.opts(), sock.named("bind")), ([], [(("10.0.0.5", 0),)]))

with section("failures are fixed sentences, never the OS's text"):
    sock, new, plat = fake("linux")
    with new, plat:
        try:
            netsock.connect_tcp(GONE, "10.0.0.40", 80)
            check("a missing port is refused", False)
        except RouteError as exc:
            eq("the route's reason", str(exc), "Dante: USB LAN is not connected to this computer.")
    eq("no socket made for a missing port", sock.calls, [])
    sock, new, plat = fake("linux", {"connect": ConnectionRefusedError(61, "Connection refused secret-os-text")})
    with new, plat:
        try:
            netsock.connect_tcp(ROUTED, "10.0.0.40", 4440)
        except RouteError as exc:
            eq("a refused connection", str(exc), "Could not connect to 10.0.0.40 port 4440 through USB LAN.")
            check("no OS text, no chained cause", "secret" not in str(exc) and exc.__cause__ is None)
    eq("closed after the failure", sock.named("close"), [()])
    sock, new, plat = fake("linux", {"bind": OSError(98, "Address already in use")})
    with new, plat:
        try:
            netsock.listen_tcp(ROUTED, 7000)
        except RouteError as exc:
            eq("a busy port", str(exc), "Port 7000 on USB LAN is already in use or cannot be opened.")
    sock, new, plat = fake("linux", {"setsockopt": OSError(19, "No such device")})
    with new, plat:
        try:
            netsock.udp_socket(ROUTED)
        except RouteError as exc:
            eq("a pin the OS refuses for another reason", str(exc), "Relay could not send through USB LAN.")

with section("multicast: sent and joined on the port's address"):
    sock, new, plat = fake("linux")
    with new, plat:
        netsock.multicast_socket(ROUTED, "239.69.1.1", 5004, ttl=16, loop=False)
    opts = sock.opts()
    check("SO_REUSEADDR set", (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in opts)
    check("pinned to en5", (socket.SOL_SOCKET, netsock.SO_BINDTODEVICE, b"en5\0") in opts)
    eq("bound to the group (Linux delivers group packets only there or to any)", sock.named("bind"), [(("239.69.1.1", 5004),)])
    check("IP_MULTICAST_IF is the port's address", (socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("10.0.0.5")) in opts)
    check("joined on the port's address, not 0.0.0.0",
          (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton("239.69.1.1") + socket.inet_aton("10.0.0.5")) in opts)
    check("TTL and loop", (socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 16) in opts and (socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0) in opts)
    sock, new, plat = fake("win32")
    with new, plat:
        netsock.multicast_socket(ROUTED, "239.69.1.1", 5004)
    eq("Windows cannot bind a group: the port's own address, joined there", sock.named("bind"), [(("10.0.0.5", 5004),)])
    sock = FakeSocket()
    netsock.join_multicast(sock, "224.0.0.251", DEFAULT)
    eq("unrouted: joined as before (any interface)", sock.opts()[1][2][4:], socket.inet_aton("0.0.0.0"))
    for bad in ("10.0.0.1", "not-an-ip"):
        try:
            netsock.join_multicast(FakeSocket(), bad, ROUTED)
            check(f"{bad} refused", False)
        except ValueError:
            pass
    sock = FakeSocket({"setsockopt": OSError(99, "x")})
    try:
        netsock.join_multicast(sock, "239.1.1.1", DEFAULT)
    except RouteError as exc:
        eq("a failed join is a fixed sentence", str(exc), "Could not join multicast group 239.1.1.1 on the chosen network port.")

with section("notes: reverse-path filtering and two ports on one subnet"):
    root = tmpdir()
    for which, value in (("all", "0"), ("en5", "1")):
        os.makedirs(os.path.join(root, which))
        Path(root, which, "rp_filter").write_text(value + "\n")
    with mock.patch.object(netsock, "PROC_CONF", root):
        eq("strict filtering on the port is read", netsock._rp_filter("en5"), 1)
        eq("an unknown interface falls back to all", netsock._rp_filter("eth9"), 0)
    with mock.patch.object(netsock, "PROC_CONF", os.path.join(root, "missing")):
        eq("no /proc: unknown", netsock._rp_filter("en5"), None)
    twin = Port(id="mac:aa:00:00:00:00:06", name="en6", friendly_name="TB LAN", ipv4=("10.0.0.9/24",), up=True)
    other = Port(id="mac:aa:00:00:00:00:01", name="en0", friendly_name="Wi-Fi", ipv4=("192.168.1.20/24",), up=True)
    eq("overlap found once, with the network", [(a.name, b.name, n) for a, b, n in netsock.overlapping([USB, twin, other])],
       [("en5", "en6", "10.0.0.0/24")])
    eq("a /16 holding a /24 overlaps", len(netsock.overlapping([USB, Port(id="x", name="en8", friendly_name="", ipv4=("10.0.7.1/16",))])), 1)
    netroute.update([USB, twin, other])
    with mock.patch.object(netsock.sys, "platform", "linux"), mock.patch.object(netsock, "_rp_filter", return_value=1):
        n = netsock.notes(ROUTED)
    check("rp_filter note", any("rp_filter=2" in x for x in n), n)
    check("same-subnet note names the other port", any("TB LAN" in x and "10.0.0.0/24" in x for x in n), n)
    eq("an unrouted class has nothing to note", netsock.notes(DEFAULT), [])
    netroute.update([])


with section("real sockets over loopback"):
    if sys.platform.startswith("linux"):
        server_at, client_at = "127.0.0.2", "127.0.0.3"   # every 127/8 address is on lo: proves the source pin
    else:
        server_at, client_at = "127.0.0.1", "127.0.0.1"
    if sys.platform in ("darwin",) or sys.platform.startswith("linux"):
        a, b = loopback_route(server_at), loopback_route(client_at)
    else:  # Windows: the unrouted path, really sending
        a = b = Resolved("test", None, None, "127.0.0.1", True, "", "fallback")
        server_at = client_at = "127.0.0.1"
    srv = netsock.listen_tcp(a, 0)
    srv.settimeout(5)
    port = srv.getsockname()[1]
    cli = netsock.connect_tcp(b, server_at, port)
    conn, peer = srv.accept()
    cli.sendall(b"ping")
    eq("TCP: received", conn.recv(4), b"ping")
    if a.routed:
        eq("TCP: left from the client route's address", peer[0], client_at)
    for s in (cli, conn, srv):
        s.close()
    rx = netsock.udp_socket(a, 0, host=server_at)   # a receiver: bound even when unrouted (Windows)
    rx.settimeout(5)
    tx = netsock.udp_socket(b)
    tx.sendto(b"dgram", (server_at, rx.getsockname()[1]))
    data, src = rx.recvfrom(64)
    eq("UDP: received", data, b"dgram")
    if a.routed:
        eq("UDP: sent from the client route's address", src[0], client_at)
    rx.close()
    tx.close()

    async def streams():
        got = []

        async def handle(reader, writer):
            got.append(await reader.readexactly(5))
            writer.write(b"hello")
            await writer.drain()
            writer.close()

        server = await netsock.start_server(a, handle, 0)
        p = server.sockets[0].getsockname()[1]
        reader, writer = await netsock.open_tcp(b, server_at, p)
        writer.write(b"howdy")
        await writer.drain()
        reply = await reader.readexactly(5)
        writer.close()
        server.close()
        await server.wait_closed()

        class Proto(asyncio.DatagramProtocol):
            def __init__(self):
                self.q = asyncio.get_running_loop().create_future()

            def datagram_received(self, d, addr):
                if not self.q.done():
                    self.q.set_result(d)

        transport, proto = await netsock.udp_endpoint(a, Proto, host=server_at)
        tx = netsock.udp_socket(b)
        tx.sendto(b"async", (server_at, transport.get_extra_info("sockname")[1]))
        dgram = await asyncio.wait_for(proto.q, 5)
        tx.close()
        transport.close()
        return got, reply, dgram

    got, reply, dgram = asyncio.run(streams())
    eq("asyncio streams both ways", (got, reply), ([b"howdy"], b"hello"))
    eq("asyncio datagram endpoint", dgram, b"async")

    async def refused():
        closed = netsock.listen_tcp(a, 0)
        p = closed.getsockname()[1]
        closed.close()
        try:
            await netsock.open_tcp(b, server_at, p, timeout=10)
            return p, "connected"
        except RouteError as exc:
            return p, str(exc)

    p, text = asyncio.run(refused())
    # Windows reports a refused connection through the unpinned path, without the port's name.
    check("open_tcp to a closed port: a fixed sentence",
          text in (f"Could not connect to {server_at} port {p} through {netsock._name(b)}.",
                   f"Could not connect to {server_at} port {p}.") and (sys.platform == "win32" or "through" in text), text)

with section("HTTPS verifies with certifi's roots when the caller passes no context"):
    import ssl
    import certifi
    ctx = netsock.tls_context()
    check("verification stays on", ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname)
    check("one shared context", netsock.tls_context() is ctx)
    roots = {c.get("serialNumber") for c in ctx.get_ca_certs()}
    bundle = ssl.create_default_context(cafile=certifi.where())
    check("certifi's roots are loaded", {c.get("serialNumber") for c in bundle.get_ca_certs()} <= roots)
    https = next(h for h in netsock.opener("translation").handlers if isinstance(h, netsock._RoutedHTTPS))
    check("an opener with no context uses it", https._context is ctx)
    own = ssl.create_default_context()
    https = next(h for h in netsock.opener("translation", context=own).handlers if isinstance(h, netsock._RoutedHTTPS))
    check("a caller's own context is kept", https._context is own)
    conn = netsock.http_connection("translation", "example.invalid", tls=True)
    check("an HTTPS connection with no context uses it", conn._context is ctx)

done()
