"""Viewers, crew pages and the operator panel served on chosen network ports (#346): which addresses each listener
binds, the lock-out notice, moving listeners while Relay runs, links, certificate names and mDNS.

On Linux every 127/8 address is on loopback, so a real server is routed to 127.0.0.2 (panel) and 127.0.0.3
(viewers) and the other addresses are refused at the socket. Elsewhere the same logic runs with 127.0.0.1 only.
Offline. Run: .venv/bin/python tests/test_net_serving.py"""
import asyncio
import http.client
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_ANNOUNCE"] = "0"
_tmp = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(_tmp, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(_tmp, "certs")
os.environ.pop("RELAY_ADMIN_IPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import announce, config, netinfo, netroute, serving, tls  # noqa: E402
from app.modules import modules, validate  # noqa: E402
from app.netinfo import Port  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402
from _netfake import policy, port, route  # noqa: E402

LINUX = sys.platform.startswith("linux")
LAN = ["192.0.2.10"]
netinfo.lan_ips = lambda: list(LAN)
CREW = port("en5", "10.0.0.5/24", "Crew LAN", mac="0a", index=5)
GUEST = port("en6", "172.16.0.9/16", "Guest Wi-Fi", mac="0b", index=6)
DANTE = port("en7", "169.254.3.3/16", "Dante", mac="0c", index=7)
config.ensure_file()
netroute.update([CREW, GUEST, DANTE])
netroute.remember([CREW, GUEST, DANTE])


with section("which addresses a listener binds"):
    route()
    eq("no route: the configured host, as before", serving.plan("viewers", "0.0.0.0"), (["0.0.0.0"], []))  # nosec B104
    route(viewers=GUEST.id, crew=CREW.id)
    eq("viewers routed: the port's address and this computer", serving.plan("viewers", "0.0.0.0")[0], ["172.16.0.9", "127.0.0.1"])  # nosec B104
    hosts, notes = serving.plan("crew", "0.0.0.0", 443, secure=True)  # nosec B104
    eq("panel routed: the crew port and this computer", hosts, ["10.0.0.5", "127.0.0.1"])
    check("a recovery note for the panel names this computer's address and config.json",
          len(notes) == 1 and "https://127.0.0.1/admin" in notes[0] and "config.json" in notes[0], notes)
    netroute.update([GUEST, DANTE])                     # the crew adapter is unplugged
    policy(crew="strict")
    hosts, notes = serving.plan("crew", "0.0.0.0", 8443, secure=True)  # nosec B104
    eq("strict and missing: this computer only, never another network", hosts, ["127.0.0.1"])
    check("it says why and that only this computer reaches it", "only this computer can reach it" in notes[0], notes)
    policy()
    eq("fallback and missing: the configured host, as before", serving.plan("crew", "0.0.0.0")[0], ["0.0.0.0"])  # nosec B104
    eq("a class nobody declared binds as configured", serving.plan("nosuch", "10.9.9.9"), (["10.9.9.9"], []))
    eq("no class at all binds as configured", serving.plan(None, "10.9.9.9"), (["10.9.9.9"], []))
    netroute.update([CREW, GUEST, DANTE])
    route()

with section("links, QR codes and certificate names follow the routes"):
    route(viewers=GUEST.id, crew=CREW.id)
    from app import main as M
    eq("viewer links: the guest port only (plus this computer)", M._urls(80), ["http://172.16.0.9/", "http://127.0.0.1/"])
    eq("panel links: the crew port", netroute.link_addresses("crew"), ["10.0.0.5"])
    route(crew=DANTE.id)
    dns, ips = tls.names()
    check("a link-local crew port is named in the certificate", "169.254.3.3" in ips, ips)
    route()
    eq("unrouted: every LAN address, as before", M._urls(80), ["http://192.0.2.10/", "http://127.0.0.1/"])
    eq("crew join page listens on the crew network", modules.get("crew").listeners[0].traffic, "crew")
    eq("signage screens default to viewers", modules.get("signage").listeners[0].traffic, "viewers")

with section("a listener names a core class or its own"):
    from app.modules import Listener, Manifest, Module, Traffic

    class Screens(Module):
        manifest = Manifest(id="screens", name="S", version="1.0.0", summary="S", settings_key="screens",
                            traffic=(Traffic("screens", "Screen pages"),))
        route_prefixes = ("/screens",)
        listeners = (Listener("a", "a", "port", 9000, ("/screens",), traffic="screens"),
                     Listener("b", "b", "port2", 9001, ("/screens",), traffic="crew"),
                     Listener("c", "c", "port3", 9002, ("/screens",), traffic="dante"))

    probs = [p for p in validate(Screens(), "screens") if "listener" in p and "traffic" in p]
    eq("its own and core's accepted; another module's refused", len(probs), 1)

with section("mDNS announces on the discovery port only, and says goodbye when it moves"):
    route(discovery=CREW.id)
    eq("announced address: the discovery port's", announce.lan_ip(), "10.0.0.5")
    policy(discovery="strict")
    netroute.update([GUEST])
    eq("strict and missing: nothing announced", announce.lan_ip(), None)
    policy()
    netroute.update([CREW, GUEST, DANTE])
    route()

    sent, joined, left = [], [], []
    addrs = iter(["10.0.0.5", "172.16.0.9", None, "172.16.0.9"])
    current = {"ip": next(addrs)}

    class FakeSock:
        def settimeout(self, t): pass
        def recvfrom(self, n): raise socket.timeout()
        def close(self): pass

    a = announce.Announcer("Test", 80)
    with mock.patch.object(announce, "open_socket", FakeSock), \
            mock.patch.object(announce, "join", lambda s, ip: joined.append(ip)), \
            mock.patch.object(announce, "leave", lambda s, ip: left.append(ip)), \
            mock.patch.object(announce, "send", lambda s, data: sent.append((current["ip"], data))), \
            mock.patch.object(announce, "lan_ip", lambda: current["ip"]), \
            mock.patch.object(announce, "RECHECK_S", 0):
        t = threading.Thread(target=a._run, daemon=True)
        t.start()
        wait_for(lambda: len(joined) >= 1)
        current["ip"] = "172.16.0.9"
        wait_for(lambda: len(joined) >= 2, timeout=3)
        current["ip"] = None
        wait_for(lambda: len(left) >= 2, timeout=3)
        current["ip"] = "172.16.0.9"
        wait_for(lambda: len(joined) >= 3, timeout=3)
        a.stop()
        t.join(5)
    eq("joined the first port, the new one, and the new one again after it came back", joined, ["10.0.0.5", "172.16.0.9", "172.16.0.9"])
    eq("left each port it stopped announcing on", left, ["10.0.0.5", "172.16.0.9"])


class FakeServer:
    def __init__(self, sock, secure):
        self.sock, self.secure, self.closed = sock, secure, False

    def close(self):
        self.closed = True
        self.sock.close()


with section("listeners move while Relay runs"):
    served = type("S", (), {"servers": []})()

    async def fake_serve(sock, secure):
        return FakeServer(sock, secure)

    route()
    g = serving.listen_group("viewers", "viewer pages", "viewers", "127.0.0.1", 0)
    port = g.port
    g.servers = {h: FakeServer(s, False) for h, s in g.socks.items()}
    g.socks = {}
    serving.publish_groups([g])
    serving._live = serving._Live(served, fake_serve)
    eq("nothing to move", asyncio.run(serving.rebind()), [])
    lo = Port(id="name:lo", name="lo0", friendly_name="Loopback port", ipv4=("127.0.0.1/8",), up=True)
    netroute.update([CREW, GUEST, DANTE, lo])
    netroute.remember([lo])
    route(viewers=lo.id)
    eq("routed to a port whose address is this computer's: same socket, nothing moved", asyncio.run(serving.rebind()), [])
    policy(viewers="strict")
    netroute.update([CREW])
    eq("its port goes away: still this computer only, nothing to move", serving.groups()[0].hosts, ["127.0.0.1"])
    route()
    with mock.patch.object(serving, "plan", return_value=(["127.0.0.1", "192.0.2.77"], [])):
        moved = asyncio.run(serving.rebind())
    eq("an address it cannot open is a notice, the rest keeps serving", (moved, serving.groups()[0].hosts), (["viewers"], ["127.0.0.1"]))
    check("the notice is a fixed sentence", any("could not listen on 192.0.2.77" in n for n in serving.notices()), serving.notices())
    with mock.patch.object(serving, "plan", return_value=([], [])):
        asyncio.run(serving.rebind())
    eq("never left with nothing: this computer kept", serving.groups()[0].hosts, ["127.0.0.1"])
    for srv in serving.groups()[0].servers.values():
        srv.close()
    serving.reset()
    netroute.update([CREW, GUEST, DANTE])


def http_get(host, port, path="/"):
    c = http.client.HTTPConnection(host, port, timeout=5)
    c.request("GET", path)
    return c.getresponse().status


def https_get(host, port, path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    c = http.client.HTTPSConnection(host, port, context=ctx, timeout=5)
    c.request("GET", path)
    return c.getresponse().status


def refused(host, port):
    try:
        socket.create_connection((host, port), timeout=2).close()
        return False
    except OSError:
        return True


with section("a real server: each class answers only on its own port's address (Linux)"):
    if not LINUX:
        print("  (only 127.0.0.1 exists on this OS's loopback: the routed addresses are checked on Linux)")
    else:
        panel_lo = Port(id="name:lo:panel", name="lo", friendly_name="Crew (lo)", ipv4=("127.0.0.2/8",), up=True,
                        index=socket.if_nametoindex("lo"))
        view_lo = Port(id="name:lo:view", name="lo", friendly_name="Guest (lo)", ipv4=("127.0.0.3/8",), up=True,
                       index=socket.if_nametoindex("lo"))
        moved_lo = Port(id="name:lo:view", name="lo", friendly_name="Guest (lo)", ipv4=("127.0.0.4/8",), up=True,
                        index=socket.if_nametoindex("lo"))
        netroute.update([panel_lo, view_lo])
        netroute.remember([panel_lo, view_lo])
        route(crew=panel_lo.id, viewers=view_lo.id)
        tls.panel = tls.Panel()
        tls.panel.start("")
        viewers = serving.listen_group("viewers", "viewer pages", "viewers", "127.0.0.1", 0)
        panel = serving.listen_group("panel", "operator panel", "crew", "127.0.0.1", 0, secure=True)
        serving.publish(viewers.port, panel.port)
        serving.publish_groups([viewers, panel])
        eq("viewers bound to their port and this computer", sorted(viewers.socks), ["127.0.0.1", "127.0.0.3"])
        eq("panel bound to the crew port and this computer", sorted(panel.socks), ["127.0.0.1", "127.0.0.2"])
        from app import main as M
        M.tls.panel = tls.panel
        server = serving.make_server("app.main:app", [], [], tls.panel.context, groups=serving.groups())
        thread = threading.Thread(target=server.run, kwargs={"sockets": []}, daemon=True)
        thread.start()
        check("the server starts", wait_for(lambda: server.started, timeout=10))
        vp, ap = viewers.port, panel.port
        eq("captions answer on the guest address", http_get("127.0.0.3", vp, "/"), 200)
        eq("the panel answers on the crew address", https_get("127.0.0.2", ap, "/admin"), 200)
        check("the viewer port on the crew address is refused at the socket", refused("127.0.0.2", vp))
        check("the panel port on the guest address is refused at the socket", refused("127.0.0.3", ap))
        eq("this computer still reaches the panel", https_get("127.0.0.1", ap, "/admin"), 200)
        # The guest port's address changes while Relay runs: the viewers move without a restart.
        netroute.update([panel_lo, moved_lo])
        moved = asyncio.run_coroutine_threadsafe(serving.rebind(), serving._live.loop).result(10)
        eq("only the viewers moved", moved, ["viewers"])
        eq("captions answer on the new address", http_get("127.0.0.4", vp, "/"), 200)
        check("the old address is refused now", refused("127.0.0.3", vp))
        eq("the panel never moved", https_get("127.0.0.2", ap, "/admin"), 200)
        server.should_exit = True
        thread.join(10)
        serving.reset()
        route()

done()
