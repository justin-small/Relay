"""Every module's traffic on the port the operator chose (#348): the HTTP, urllib and WebSocket helpers in
app/netsock.py, each module's traffic class and the helper it goes through, the sentences on failure, health for a
route that fell back, and the reconnect when a port changes. Offline: loopback servers and mocks, no second NIC.
Run: .venv/bin/python tests/test_module_traffic.py"""
import asyncio
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(TMP, "certs")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"

import websockets  # noqa: E402

from app import config, netinfo, netroute, netsock  # noqa: E402
from app.modules import Health, modules  # noqa: E402
from app.netinfo import Port  # noqa: E402
from app.netroute import Resolved  # noqa: E402
from app.netsock import RouteError  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section  # noqa: E402

WIFI = Port(id="mac:aa:00:00:00:00:01", name="en0", friendly_name="Wi-Fi", ipv4=("192.168.1.20/24",),
            default_route=True, up=True, kind="wifi")
USB = Port(id="mac:aa:00:00:00:00:05", name="en5", friendly_name="USB LAN", ipv4=("10.0.0.5/24",), up=True,
           kind="ethernet", index=7)
GONE = Resolved("atem", USB.id, None, None, False, "ATEM switcher control (UDP): USB LAN is not connected to this "
                "computer.", "strict")

config.ensure_file()


def loopback_route(address="127.0.0.1"):
    """A class routed to the loopback port: real pinned sockets without a second network card (as test_netsock)."""
    if sys.platform == "win32":
        return Resolved("test", None, None, "127.0.0.1", True, "", "fallback")
    name = "lo" if sys.platform.startswith("linux") else "lo0"
    port = Port(id=f"name:{name}", name=name, friendly_name="Loopback", ipv4=(f"{address}/8",), up=True,
                index=socket.if_nametoindex(name))
    return Resolved("test", port.id, port, address, True, "", "strict")


class Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"from": self.client_address[0], "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def http_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ the helpers, over real loopback sockets

with section("http_connection: http.client through the route"):
    srv = http_server()
    route = loopback_route()
    conn = netsock.http_connection(route, "127.0.0.1", srv.server_address[1], timeout=5)
    conn.request("GET", "/version")
    resp = conn.getresponse()
    eq("answered", (resp.status, json.loads(resp.read())["path"]), (200, "/version"))
    conn.close()
    try:
        netsock.http_connection(GONE, "127.0.0.1", srv.server_address[1], timeout=5).request("GET", "/")
        check("an unusable route refuses", False)
    except RouteError as exc:
        eq("its reason, a fixed sentence", str(exc), GONE.reason)
    srv.shutdown()

with section("urlopen and opener: urllib through the route"):
    srv = http_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/plans"
    with netsock.urlopen(loopback_route(), url, timeout=5) as resp:
        eq("urlopen answered", json.loads(resp.read())["path"], "/plans")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    with netsock.opener(loopback_route(), NoRedirect).open(url, timeout=5) as resp:
        eq("an opener with extra handlers answered", resp.status, 200)
    try:
        netsock.opener("atem", urllib.request.HTTPSHandler())
        check("an HTTPS handler of one's own is refused (it would replace the routed one)", False)
    except ValueError:
        check("an HTTPS handler of one's own is refused (it would replace the routed one)", True)
    try:
        netsock.urlopen(GONE, url, timeout=5)
        check("an unusable route refuses", False)
    except urllib.error.URLError as exc:
        check("urllib wraps the RouteError, an OSError, as a URLError",
              isinstance(exc.reason, RouteError) and str(exc.reason) == GONE.reason, repr(exc.reason))
    srv.shutdown()

with section("websocket: websockets through the route"):
    async def ws_round_trip():
        async def echo(ws, path=None):
            async for msg in ws:
                await ws.send("echo " + msg)

        server = await websockets.serve(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with netsock.websocket_session(loopback_route(), f"ws://127.0.0.1:{port}/socket.io/",
                                                 open_timeout=5) as ws:
                await ws.send("40")
                got = await asyncio.wait_for(ws.recv(), 5)
        finally:
            server.close()
            await server.wait_closed()
        return got

    eq("a frame there and back", asyncio.run(ws_round_trip()), "echo 40")
    try:
        asyncio.run(netsock.websocket("atem", "http://example.com/"))
        check("only ws:// and wss://", False)
    except ValueError:
        check("only ws:// and wss://", True)

    async def refused():
        try:
            await netsock.websocket(GONE, "ws://127.0.0.1:9/", open_timeout=1)
        except RouteError as exc:
            return str(exc)
    eq("an unusable route refuses before connecting", asyncio.run(refused()), GONE.reason)

with section("udp_endpoint with a remote, and multicast_sender"):
    async def udp_round_trip():
        rx = netsock.udp_socket(loopback_route(), 0, host="127.0.0.1")
        rx.settimeout(5)
        transport, _ = await netsock.udp_endpoint(loopback_route(), asyncio.DatagramProtocol,
                                                  remote=("127.0.0.1", rx.getsockname()[1]))
        transport.sendto(b"hello")      # connected: no address needed, as with remote_addr
        data = await asyncio.to_thread(rx.recv, 64)
        transport.close()
        rx.close()
        return data

    eq("sent to the remote it was connected to", asyncio.run(udp_round_trip()), b"hello")
    s = netsock.multicast_sender(loopback_route(), ttl=255)
    eq("TTL set", s.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL), 255)
    s.close()

with section("failure(): a sentence built from the route, never from an exception"):
    netroute.update([WIFI, USB])
    routed = Resolved("atem", USB.id, USB, "10.0.0.5", True, "", "fallback")
    eq("routed: the default plus the port it went through", netsock.failure(routed, "Could not reach the switcher"),
       "Could not reach the switcher. It goes out through USB LAN (10.0.0.5).")
    eq("unusable: the route's reason", netsock.failure(GONE, "Could not reach the switcher"), GONE.reason)
    fell = Resolved("atem", USB.id, None, "192.168.1.20", True, "USB LAN is gone. Using the system's default "
                    "network instead.", "fallback", True)
    eq("fell back: says so", netsock.failure(fell, "Could not reach the switcher."),
       "Could not reach the switcher. USB LAN is gone. Using the system's default network instead.")
    eq("unrouted: the default as it was",
       netsock.failure(Resolved("atem", None, None, "192.168.1.20", True), "Could not reach the switcher"),
       "Could not reach the switcher")
    eq("a class nobody declared: the default", netsock.failure("nosuch", "No."), "No.")

# ------------------------------------------------------------------ each module declares its traffic

with section("every module that talks to something outside Relay declares a class"):
    cls = netroute.classes()
    want = {"translation": None, "atem": "atem", "propresenter": "propresenter", "tally_arbiter": "tally_arbiter",
            "planning_center": "planning_center", "remote_access": "remote_access", "intercom": "intercom",
            "crew.push": "crew", "signage_web": "signage_web", "alert_events": "alert_events"}
    eq("the classes and their owners", {c: cls[c][0] for c in want if c in cls}, want)
    eq("intercom's media is strict: never on another network", cls["intercom"][1].default_policy, "strict")
    eq("the internet ones fall back", {cls[c][1].default_policy for c in
                                       ("translation", "planning_center", "remote_access", "crew.push")}, {"fallback"})

# ------------------------------------------------------------------ and goes through the helper with it

with section("ATEM: UDP to the switcher through 'atem'"):
    from app.modules.atem.client import AtemClient

    async def atem():
        c = AtemClient("10.0.0.40", 9910)
        seen = {}

        async def fake(route, factory, port=0, *, remote=None, **kw):
            seen.update(route=route, remote=remote)
            raise RouteError(GONE.reason)

        with mock.patch.object(netsock, "udp_endpoint", fake), \
                mock.patch.object(netroute, "resolve", lambda cid: GONE):
            c.start()
            for _ in range(100):
                if c.error:
                    break
                await asyncio.sleep(0.01)
            await c.close()
        return seen, c.error

    seen, error = asyncio.run(atem())
    eq("its class and the switcher", seen, {"route": "atem", "remote": ("10.0.0.40", 9910)})
    eq("the page says why, from the route", error, GONE.reason)

with section("Tally Arbiter: its WebSocket through 'tally_arbiter'"):
    from app.modules.tally_arbiter.listener import Listener

    async def tally():
        lst = Listener("10.0.0.41", 4455)
        seen = {}

        async def fake(route, url, **kw):
            seen.update(route=route, url=url)
            raise RouteError("x")

        with mock.patch.object(netsock, "websocket", fake):   # websocket_session opens through it
            lst.start()
            for _ in range(100):
                if lst.error:
                    break
                await asyncio.sleep(0.01)
            lst._stop.set()
            lst._task.cancel()
            await asyncio.gather(lst._task, return_exceptions=True)
        return seen, lst.error

    seen, error = asyncio.run(tally())
    eq("its class", seen["route"], "tally_arbiter")
    check("the Socket.IO address", seen["url"].startswith("ws://10.0.0.41:4455/socket.io/"), seen["url"])
    eq("a fixed sentence", error, "Could not reach Tally Arbiter")

with section("ProPresenter: its API and its mDNS search through 'propresenter'"):
    from app.modules.propresenter import propresenter as pp

    srv = http_server()
    seen = []
    real = netsock.http_connection

    def spy(route, *a, **kw):
        seen.append(route)
        return real(route, *a, **kw)

    with mock.patch.object(netsock, "http_connection", spy):
        out = pp.Client("127.0.0.1", srv.server_address[1]).read("/v1/status/slide")
    eq("asked through its class", seen, ["propresenter"])
    eq("and answered", out["path"], "/v1/status/slide")
    srv.shutdown()
    asked = []

    def sender(route, **kw):
        asked.append((route, kw))
        raise RouteError("x")

    with mock.patch.object(netsock, "multicast_sender", sender):
        eq("a search that cannot ask finds nothing", pp.discover(0.1), [])
    eq("asked on its class, TTL 255", asked, [("propresenter", {"ttl": 255})])

with section("Planning Center: its API through 'planning_center', and the sentence names the port"):
    from app.modules.planning_center import planningcenter as pc

    srv = http_server()
    seen = []
    real_urlopen = netsock.urlopen

    def spy_urlopen(route, req, **kw):
        seen.append(route)
        return real_urlopen(route, req, **kw)

    client = pc.Client("app", "secret", base_url=f"http://127.0.0.1:{srv.server_address[1]}")
    with mock.patch.object(netsock, "urlopen", spy_urlopen):
        out = client._request("GET", "/services/v2/service_types", None)
    eq("asked through its class, and answered", (seen, out["path"]), (["planning_center"], "/services/v2/service_types"))
    srv.shutdown()
    netroute.update([WIFI])
    with mock.patch.object(netroute, "resolve", lambda cid: Resolved(cid, USB.id, None, None, False,
                                                                     "Planning Center (over the internet): USB LAN "
                                                                     "is not connected to this computer.", "strict")):
        eq("operator sentence when its port is gone", pc.operator_message(pc.PlanningCenterError("network", "x")),
           "Planning Center (over the internet): USB LAN is not connected to this computer.")
    eq("unrouted: the sentence as before", pc.operator_message(pc.PlanningCenterError("network", "x")),
       "Could not reach Planning Center. Check this machine's internet connection.")

with section("Remote access: the cloud through 'remote_access', and cloudflared told the address"):
    from app.modules.remote_access import cloud, tunnel

    seen = []

    def fake_urlopen(route, req, **kw):
        seen.append(route)
        raise urllib.error.URLError(RouteError("x"))

    with mock.patch.object(netsock, "urlopen", fake_urlopen):
        try:
            cloud._call("https://cloud.example", "GET", "/v1/health")
        except cloud.CloudError as exc:
            eq("a network error", exc.kind, "network")
    eq("asked through its class", seen, ["remote_access"])
    eq("unrouted: cloudflared chooses", tunnel.edge_bind_args(), [])
    up = Resolved("remote_access", USB.id, USB, "10.0.0.5", True, "", "fallback")
    with mock.patch.object(netroute, "resolve", lambda cid: up):
        eq("routed: bound to the port's address", tunnel.edge_bind_args(), ["--edge-bind-address", "10.0.0.5"])
    fell = Resolved("remote_access", USB.id, None, "192.168.1.20", True, "gone", "fallback", True)
    with mock.patch.object(netroute, "resolve", lambda cid: fell):
        eq("fell back: cloudflared chooses again", tunnel.edge_bind_args(), [])

with section("Intercom: LiveKit advertises and gathers on the intercom port only"):
    from app.modules.intercom import livekit

    with mock.patch.object(netinfo, "lan_ips", lambda: ["192.168.1.20"]):
        eq("unrouted: the first LAN address, as before", livekit.media_route(""), ("192.168.1.20", "", ""))
    eq("an address typed under Settings wins", livekit.media_route("10.9.9.9"), ("10.9.9.9", "", ""))
    up = Resolved("intercom", USB.id, USB, "10.0.0.5", True, "", "strict")
    with mock.patch.object(netroute, "resolve", lambda cid: up):
        eq("routed: the port's address and interface", livekit.media_route(""), ("10.0.0.5", "en5", ""))
    gone = Resolved("intercom", USB.id, None, None, False, "Intercom audio (LiveKit media): USB LAN is not "
                    "connected to this computer.", "strict")
    with mock.patch.object(netroute, "resolve", lambda cid: gone):
        eq("strict and gone: LiveKit does not start, and says why", livekit.media_route(""), ("", "", gone.reason))
    text = livekit.config_text("10.0.0.5", "relay", "", "en5")
    check("rtc.interfaces names the port", '  interfaces:\n    includes:\n      - "en5"' in text, text)
    check("rtc.ips keeps candidates to its address", '      - "10.0.0.5/32"' in text, text)
    check("unrouted: no interface filter", "interfaces:" not in livekit.config_text("10.0.0.5", "relay"))

    async def launch_gone():
        sup = livekit.Supervisor()
        sup.settings = {"node_ip": ""}
        with mock.patch.object(netroute, "resolve", lambda cid: gone):
            started = await sup._launch("/nonexistent/livekit-server")
        return started, sup.state, sup.detail

    eq("the supervisor refuses to start it", asyncio.run(launch_gone()), (False, "error", gone.reason))

with section("Alert events: webhooks and OSC through 'alert_events'"):
    from app.modules.alert_events import events

    seen = []

    def fake_urlopen(route, req, **kw):
        seen.append(route)
        raise urllib.error.URLError(RouteError("x"))

    with mock.patch.object(netsock, "urlopen", fake_urlopen):
        eq("webhook: a fixed sentence", events.send_webhook("http://10.0.0.9/hook", {"event": "alert.sent"}),
           "The webhook could not be reached.")
    sent = []

    class Rec:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):
            pass

        def sendto(self, data, addr):
            sent.append(addr)

    def fake_udp(route, *a, **kw):
        seen.append(route)
        return Rec()

    with mock.patch.object(netsock, "udp_socket", fake_udp):
        eq("OSC sent", events.send_osc("10.0.0.9", 53000, {"event": "alert.sent"}), "")
    eq("both through its class", seen, ["alert_events", "alert_events"])
    eq("to the OSC host", sent, [("10.0.0.9", 53000)])

with section("Crew notifications and signage link checks use their own classes"):
    from app.modules.crew import webpush
    from app.modules.signage_web import links

    seen = []

    def fake_opener(route, *handlers, **kw):
        seen.append(route)
        raise urllib.error.URLError(RouteError("x"))

    with mock.patch.object(netsock, "opener", fake_opener):
        try:
            webpush.post("https://fcm.googleapis.com/fcm/send/x", {}, b"")
        except webpush.Failed:
            pass
    eq("web push through 'crew.push'", seen, ["crew.push"])
    eq("signage checks through 'signage_web'", links.TRAFFIC, "signage_web")
    check("its opener is a routed one", any(isinstance(h, netsock._RoutedHTTPS) for h in links._OPENER.handlers))

with section("Translation (core): OpenAI through 'translation'"):
    from app import realtime

    seen = []

    async def fake_ws(route, url, **kw):
        seen.append((route, url))
        return "ws"

    with mock.patch.object(netsock, "websocket", fake_ws):
        eq("connected", asyncio.run(realtime._connect("wss://api.openai.com/v1/realtime", {"A": "b"})), "ws")
    eq("through its class", seen, [("translation", "wss://api.openai.com/v1/realtime")])

# ------------------------------------------------------------------ health and port changes

with section("health: a fallback route whose port is gone says so"):
    netroute.update([WIFI])
    config.save({"modules": {"tally_arbiter": {"enabled": True}},
                 "network": {"known_ports": {USB.id: {"name": "en5", "friendly_name": "USB LAN", "kind": "ethernet"}},
                             "routes": {"tally_arbiter": USB.id}}})
    mod = modules.get("tally_arbiter")
    with mock.patch.object(netinfo, "lan_ips", lambda: ["192.168.1.20"]), \
            mock.patch.object(type(mod), "health", lambda self: Health("ok", "Connected")):
        h = modules.health(mod)
    eq("warn, naming the port, then the module's own", (h.state, h.detail),
       ("warn", "Tally Arbiter (WebSocket): USB LAN is not connected to this computer. Using the system's default "
                "network instead. Connected"))
    config.save({"network": {"routes": {}}}, replace=("network",))

with section("a port change reconnects each module through where it goes now"):
    async def changed():
        calls = []

        async def note(name):
            calls.append(name)

        from app.modules.atem import service as atem_service
        from app.modules.propresenter import propresenter as pp_mod
        from app.modules.remote_access import tunnel as ra_tunnel
        from app.modules.tally_arbiter import listener as ta

        with mock.patch.object(atem_service.service, "restart", lambda: note("atem")), \
                mock.patch.object(ta.service, "restart", lambda: note("tally_arbiter")), \
                mock.patch.object(pp_mod.follower, "reconnect", lambda: note("propresenter")), \
                mock.patch.object(ra_tunnel.tunnel, "restart", lambda: note("remote_access")):
            for mid in ("atem", "tally_arbiter", "propresenter", "remote_access"):
                modules.get(mid).network_changed({})
            await asyncio.sleep(0.05)
        return sorted(calls)

    eq("each one reconnected", asyncio.run(changed()), ["atem", "propresenter", "remote_access", "tally_arbiter"])

done()
