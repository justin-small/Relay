"""The network map (#437): the mDNS packet reader, the merge of every source, a module's feed, and the page's API.

Offline: packets are built by hand, mDNS and the network ports are faked, no device is touched.
Run: .venv/bin/python tests/test_network_map.py
"""
import asyncio
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import gear, config, db, mdns, netinfo, network_map, ports, users  # noqa: E402
from app import main as M  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import Module  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

users.SCRYPT_N = 2 ** 10
ADMIN = "http://relay.local:8001"
config.ensure_file()
config.save({"port": 8000, "admin_port": 8001, "blocklist_file": str(Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt")})
db.init()


# ------------------------------------------------------------------ packet builders
def name(n):
    return b"".join(bytes([len(x)]) + x.encode() for x in n.split(".")) + b"\0"


def rr(n, rtype, rdata, ttl=120):
    return (n if isinstance(n, bytes) else name(n)) + struct.pack(">HHIH", rtype, 1, ttl, len(rdata)) + rdata


def packet(*records):
    return struct.pack(">6H", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


NDI = "_ndi._tcp.local"
INST = "STAGE-PC (OBS)._ndi._tcp.local"

with section("reading packets"):
    ptr = rr(NDI, mdns.TYPE_PTR, name(INST))
    # SRV: priority, weight, port, then the target
    srv = rr(INST, mdns.TYPE_SRV, struct.pack(">HHH", 0, 0, 5961) + name("stage-pc.local"))
    a = rr("Stage-PC.local", mdns.TYPE_A, bytes([192, 168, 1, 20]))
    txt = rr(INST, mdns.TYPE_TXT, bytes([9]) + b"ver=5.6.0" + bytes([4]) + b"x=yz")
    recs = mdns.parse(packet(ptr, srv, a, txt))
    eq("four records", [r.rtype for r in recs], [mdns.TYPE_PTR, mdns.TYPE_SRV, mdns.TYPE_A, mdns.TYPE_TXT])
    eq("PTR names the instance", recs[0].value, INST)
    eq("SRV has port and target", recs[1].value, (5961, "stage-pc.local"))
    eq("A is an address, its name in lower case", (recs[2].name, recs[2].value), ("stage-pc.local", "192.168.1.20"))
    eq("TXT is key and value", recs[3].value, {"ver": "5.6.0", "x": "yz"})
    # a compression pointer back to the question name
    first = name(NDI)
    body = first + struct.pack(">HHIH", mdns.TYPE_PTR, 1, 120, 2) + b"\xc0\x0c"
    eq("a pointer is followed", mdns.parse(struct.pack(">6H", 0, 0x8400, 0, 1, 0, 0) + body)[0].value, NDI)
    loop = struct.pack(">6H", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c" + b"\0" * 10
    eq("a pointer loop is refused", mdns.parse(loop), [])
    eq("junk is refused", mdns.parse(b"\x00\x01"), [])
    eq("a cut-off packet is refused, not a crash", mdns.parse(packet(ptr)[:-5]), [])
    q = mdns.build_query([(NDI, mdns.TYPE_PTR)])
    eq("a query has one question", struct.unpack(">6H", q[:12])[2], 1)
    check("…with the unicast bit set", q.endswith(struct.pack(">HH", mdns.TYPE_PTR, 0x8001)))

with section("following up an answer that has no host (UniFi's mDNS proxy)"):
    found = {}
    mdns.collect([packet(ptr)], found)
    f = found[INST]
    eq("the PTR alone gives the instance", (f.label, f.host, f.port, f.ips), ("STAGE-PC (OBS)", "", None, []))
    mdns.collect([packet(ptr), packet(srv, txt)], found)
    eq("the SRV later gives host and port", (f.host, f.port), ("stage-pc.local", 5961))
    mdns.collect([packet(ptr), packet(srv, txt), packet(a)], found)
    eq("the A record later gives the address", f.ips, ["192.168.1.20"])
    eq("TXT arrives too", f.txt["ver"], "5.6.0")
    other = {}
    mdns.collect([packet(rr("_other._tcp.local", mdns.TYPE_PTR, name("x._other._tcp.local")))], other)
    eq("a service type nobody asked for is ignored", other, {})


with section("scanning over real sockets (a fake responder on this computer)"):
    import socket
    import threading

    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.settimeout(0.2)
    stop = threading.Event()
    asked = []

    def responder():
        # Like UniFi's proxy: the first answer has the PTR only, from an address that is not the device's.
        while not stop.is_set():
            try:
                data, addr = srv_sock.recvfrom(2000)
            except socket.timeout:
                continue
            q = []
            pos = 12
            for _ in range(struct.unpack(">H", data[4:6])[0]):
                n, pos = mdns._name(data, pos)
                t = struct.unpack(">H", data[pos:pos + 2])[0]
                pos += 4
                q.append((n, t))
            asked.extend(q)
            for n, t in q:
                if t == mdns.TYPE_PTR and n == NDI:
                    srv_sock.sendto(packet(ptr), addr)
                elif t == mdns.TYPE_SRV and n == INST:
                    srv_sock.sendto(packet(srv, txt), addr)
                elif t == mdns.TYPE_A and n == "stage-pc.local":
                    srv_sock.sendto(packet(a), addr)

    th = threading.Thread(target=responder, daemon=True)
    th.start()
    real_group = mdns.GROUP
    mdns.GROUP = ("127.0.0.1", srv_sock.getsockname()[1])
    try:
        got = mdns.scan(["127.0.0.1"], 0.6)
    finally:
        mdns.GROUP = real_group
        stop.set()
        th.join(1)
        srv_sock.close()
    eq("one instance found", [x.label for x in got], ["STAGE-PC (OBS)"])
    eq("its host, port and address came from follow-up questions", (got[0].host, got[0].port, got[0].ips), ("stage-pc.local", 5961, ["192.168.1.20"]))
    check("it asked for the SRV, then the A record", (INST, mdns.TYPE_SRV) in asked and ("stage-pc.local", mdns.TYPE_A) in asked, asked)
    eq("an interface that cannot send is skipped, no interfaces is empty", mdns.scan([], 0.1), [])


# ------------------------------------------------------------------ merging
def src(sid, label, devices, stale=None):
    return {"id": sid, "label": label, "stale": stale, "devices": devices}


with section("cleaning what a source sends"):
    eq("nothing to tell it apart by is dropped", network_map.clean_device({"name": "x"}), None)
    eq("not a dict is dropped", network_map.clean_device("x"), None)
    d = network_map.clean_device({"mac": "AA-BB-CC-DD-EE-FF", "ip": "999.1.1.1", "ips": ["10.0.0.5", "10.0.0.5", 5],
                                  "host": "Stage.local", "name": "x" * 300, "kind": "toaster", "ndi": "yes", "errors": True,
                                  "tags": ["a", "", 3], "ports": [{"port": "x"}, {"port": 3, "up": 1}]})
    eq("a MAC is lower case with colons", d["mac"], "aa:bb:cc:dd:ee:ff")
    eq("a bad address is dropped, a repeat kept once", d["ips"], ["10.0.0.5"])
    eq("a host name loses .local and case", d["host"], "stage")
    eq("a name is cut", len(d["name"]), 80)
    eq("an unknown kind is nothing", d["kind"], "")
    eq("a flag must be a real boolean", d["ndi"], None)
    eq("a bool is not an error count", d["errors"], None)
    eq("tags keep only text", d["tags"], ["a"])
    eq("a port needs a number", [p["port"] for p in d["ports"]], [3])

with section("merging devices"):
    out = network_map.merge([
        src("unifi", "UniFi", [{"mac": "aa:aa:aa:aa:aa:01", "ip": "10.0.0.2", "name": "Core switch", "kind": "switch"},
                               {"mac": "aa:aa:aa:aa:aa:02", "ip": "10.0.0.20", "name": "Stage box 1", "uplink_mac": "aa:aa:aa:aa:aa:01",
                                "uplink_port": 4, "dante": True, "identified_by": ["traffic flows"]}]),
        src("discovery", "Discovery (mDNS)", [{"ip": "10.0.0.20", "host": "stagebox1", "dante": True, "identified_by": ["mDNS _netaudio-arc"]},
                                              {"ip": "10.0.0.30", "host": "ndi-cam", "name": "NDI CAM", "ndi": True}])])
    by = {d["name"]: d for d in out}
    eq("three devices (the box seen twice is one)", len(out), 3)
    box = by["Stage box 1"]
    eq("merged by IP when the second source has no MAC", (box["mac"], box["seen_by"]), ("aa:aa:aa:aa:aa:02", ["unifi", "discovery"]))
    eq("how it was identified, from both", box["identified_by"], ["traffic flows", "mDNS _netaudio-arc"])
    eq("its place comes from the switch source", (box["parent"], box["uplink_port"]), (by["Core switch"]["id"], 4))
    eq("a discovered device with nothing else is AV", by["NDI CAM"]["kind"], "av")
    eq("the first source's name wins", box["name"], "Stage box 1")

    out = network_map.merge([src("a", "Module A", [{"ip": "10.0.0.9", "name": "Mixer", "ndi": True}]),
                             src("b", "Module B", [{"ip": "10.0.0.9", "ndi": False}])])
    eq("any source saying NDI makes it NDI", out[0]["ndi"], True)
    check("…and disagreement leaves a note", len(out[0]["notes"]) == 1 and "Module B" in out[0]["notes"][0], out[0]["notes"])
    out = network_map.merge([src("b", "Module B", [{"ip": "10.0.0.9", "ndi": False}])])
    eq("a source saying no, with nobody saying yes, is no", out[0]["ndi"], False)

    out = network_map.merge([src("discovery", "Discovery", [{"ip": "192.168.1.20", "host": "stage-mac", "dante": True},
                                                            {"ip": "10.20.0.20", "host": "stage-mac", "dante": True}])])
    eq("a host with a port on two networks is one device", (len(out), sorted(out[0]["ips"])), (1, ["10.20.0.20", "192.168.1.20"]))

    out = network_map.merge([src("a", "A", [{"mac": "aa:aa:aa:aa:aa:03", "name": "x"}, {"mac": "aa:aa:aa:aa:aa:03", "ip": "10.0.0.3"}])])
    eq("the same MAC twice is one device", (len(out), out[0]["ips"]), (1, ["10.0.0.3"]))

    out = network_map.merge([src("unifi", "UniFi", [{"ip": "10.0.0.50", "name": "Old read"}], stale=95)])
    eq("devices from a failed source are marked", out[0]["stale_by"], ["unifi"])

with section("putting a device on one of Relay's ports"):
    devs = network_map.merge([src("d", "D", [{"ip": "192.168.1.20", "name": "a"}, {"ip": "10.9.9.9", "name": "b"}])])
    network_map.assign_ports(devs, [{"id": "p1", "ipv4": ["192.168.1.5/24"]}, {"id": "p2", "ipv4": ["10.0.0.5/24"]}])
    eq("by subnet", {d["name"]: d["via"] for d in devs}, {"a": "p1", "b": ""})


# ------------------------------------------------------------------ the modules' feeds
with section("what the modules add"):
    from app.modules.unifi.service import Service

    svc = Service()
    svc.parts["gear"] = [
        {"mac": "aa:aa:aa:aa:aa:01", "name": "Core", "model": "USW-Pro", "kind": "switch", "ip": "10.0.0.2", "uplink": None, "poe_w": None,
         "ports": [{"port": 4, "name": "Stage box 1 Top", "up": True, "speed": 1000, "poe_w": None, "errors": 0}]},
        {"mac": "aa:aa:aa:aa:aa:02", "name": "Gateway", "model": "UDM", "kind": "gateway", "ip": "10.0.0.1", "uplink": None, "ports": []}]
    svc.parts["av"] = {"devices": [{"mac": "aa:aa:aa:aa:aa:20", "kinds": ["dante", "ptp"]}]}

    class Presence:
        def online(self):
            return [{"mac": "aa:aa:aa:aa:aa:20", "ip": "10.0.0.20", "hostname": "stagebox", "name": "Stage box 1", "wired": True,
                     "uplink": "aa:aa:aa:aa:aa:01", "port": 4},
                    {"mac": "aa:aa:aa:aa:aa:21", "ip": "10.0.0.21", "hostname": "phone", "name": "", "wired": False, "uplink": "aa:aa:aa:aa:aa:99"}]
    svc.presence = Presence()
    got = {d["mac"]: d for d in svc.map_devices()}
    eq("UniFi gives its gear and its clients", len(got), 4)
    eq("a client is on its switch and port", (got["aa:aa:aa:aa:aa:20"]["uplink_mac"], got["aa:aa:aa:aa:aa:20"]["uplink_port"]),
       ("aa:aa:aa:aa:aa:01", 4))
    eq("the port's name and speed come with it", (got["aa:aa:aa:aa:aa:20"]["port_name"], got["aa:aa:aa:aa:aa:20"]["speed"]), ("Stage box 1 Top", 1000))
    eq("Dante from the traffic flows", (got["aa:aa:aa:aa:aa:20"]["dante"], got["aa:aa:aa:aa:aa:20"]["ndi"]), (True, False))
    eq("a client with no flow data says nothing", got["aa:aa:aa:aa:aa:21"]["dante"], None)
    eq("a Wi-Fi client has no port", got["aa:aa:aa:aa:aa:21"]["uplink_port"], None)
    merged = network_map.merge([src("unifi", "UniFi", list(got.values()))])
    by = {d["name"]: d for d in merged}
    eq("through the merge the client sits under its switch", by["Stage box 1"]["parent"], by["Core"]["id"])
    eq("the gateway is a gateway", by["Gateway"]["kind"], "gateway")

    config.save({"blackmagic": {"devices": [{"id": "main", "name": "Main room", "host": "192.168.1.240", "port": 9910},
                                      {"id": "b", "name": "Overflow", "host": "atem-b.local", "port": 9910}]},
                 "obs": {"machines": [{"id": "m1", "name": "Stream PC", "host": "192.168.1.31", "port": 4455}]},
                 "propresenter": {"host": "192.168.1.50", "port": 1025}}, replace=("blackmagic", "obs", "propresenter"))
    from app.modules import discover
    mods = discover()
    atem = mods["blackmagic"].network_map()
    eq("ATEM names each switcher, by address or by name", [(d.get("ip"), d.get("host"), d["name"]) for d in atem],
       [("192.168.1.240", None, "Main room"), (None, "atem-b.local", "Overflow")])
    eq("OBS marks its machines", [(d["ip"], d["tags"]) for d in mods["obs"].network_map()], [("192.168.1.31", ["OBS Studio"])])
    eq("ProPresenter marks its computer", [(d["ip"], d["tags"]) for d in mods["propresenter"].network_map()], [("192.168.1.50", ["ProPresenter"])])
    eq("a module with nothing to add adds nothing", mods["youtube"].network_map(), [])
    eq("…and says so", mods["youtube"].network_map_adds, "")
    named = network_map.merge([src("discovery", "Discovery", [{"ip": "192.168.1.31", "ndi": True}]), src("obs", "OBS Studio", mods["obs"].network_map())])
    eq("OBS names a computer discovery found", (named[0]["name"], named[0]["tags"], named[0]["ndi"]), ("Stream PC", ["OBS Studio"], True))


# ------------------------------------------------------------------ the page's API, with fakes
class FakeModule(Module):
    from app.modules import Manifest
    manifest = Manifest(id="fakemap", name="Fake gear", summary="x", settings_key="fakemap", guide="", version="1")
    network_map_adds = "Fake switches."
    broken = False

    def network_map(self):
        if self.broken:
            raise RuntimeError("secret detail that must not reach the page")
        return [{"mac": "aa:aa:aa:aa:aa:10", "ip": "10.0.0.2", "name": "Fake switch", "kind": "switch"}]


class FakeRegistry:
    def __init__(self, mod, on):
        self.mod, self.on = mod, on

    def all(self):
        return [self.mod]

    def enabled(self, mid):
        return self.on


PORT = netinfo.Port(id="aa:bb", name="en0", friendly_name="Ethernet", mac="aa:bb:cc:dd:ee:ff", ipv4=("10.0.0.5/24",), default_route=True,
                    up=True, kind="ethernet")
netinfo.interfaces = lambda include_hidden=False: [PORT]
mdns.scan = lambda ips, seconds=2.0, services=mdns.SERVICES: [mdns.Found("_ndi._tcp.local", "CAM 1._ndi._tcp.local", "cam1.local", 5961, ["10.0.0.30"])]

with section("the snapshot"):
    mod = FakeModule()
    network_map.reset()
    snap = asyncio.run(network_map.snapshot(FakeRegistry(mod, True), force=True))
    names = {d["name"] for d in snap["devices"]}
    check("a module's device is on the map", "Fake switch" in names, names)
    check("a discovered NDI source is on the map", any(d["ndi"] for d in snap["devices"]))
    check("Relay itself is there", any(d["kind"] == "relay" for d in snap["devices"]))
    eq("a switch source was found", snap["has_switch_source"], True)
    eq("the sources card lists the module, Relay and discovery", sorted(s["id"] for s in snap["sources"]), ["discovery", "fakemap", "relay"])
    eq("the module says what it adds", [s["adds"] for s in snap["sources"] if s["id"] == "fakemap"], ["Fake switches."])

    network_map.reset()
    snap = asyncio.run(network_map.snapshot(FakeRegistry(mod, False), force=True))
    check("a module that is off adds nothing", "Fake switch" not in {d["name"] for d in snap["devices"]})
    eq("…and is listed as off", [s["on"] for s in snap["sources"] if s["id"] == "fakemap"], [False])
    eq("so there is no switch source", snap["has_switch_source"], False)
    check("the NDI source is placed on a Relay port", [d["via"] for d in snap["devices"] if d["name"] == "CAM 1"] == ["aa:bb"])

    network_map.reset()
    asyncio.run(network_map.snapshot(FakeRegistry(mod, True), force=True))
    mod.broken = True
    snap = asyncio.run(network_map.snapshot(FakeRegistry(mod, True), force=True))
    s = [x for x in snap["sources"] if x["id"] == "fakemap"][0]
    check("a failing source says so with a fixed sentence", s["error"] == "This source could not be read." and "secret" not in str(snap))
    check("…keeps its last good devices, with their age", s["stale"] is not None and "Fake switch" in {d["name"] for d in snap["devices"]})
    check("…and marks those devices", [d["stale_by"] for d in snap["devices"] if d["name"] == "Fake switch"] == [["fakemap"]])
    check("the rest stays live", any(d["name"] == "CAM 1" and not d["stale_by"] for d in snap["devices"]))

    network_map.reset()
    calls = []
    mdns.scan = lambda ips, seconds=2.0, services=mdns.SERVICES: calls.append(1) or []
    asyncio.run(network_map.snapshot(FakeRegistry(mod, True)))
    asyncio.run(network_map.snapshot(FakeRegistry(mod, True)))
    eq("a fresh snapshot is served again, not rebuilt", len(calls), 1)
    asyncio.run(network_map.snapshot(FakeRegistry(mod, True), force=True))
    eq("Refresh rebuilds it", len(calls), 2)

with section("discovery that cannot listen"):
    network_map.reset()

    def boom(ips, seconds=2.0, services=mdns.SERVICES):
        raise OSError("no multicast")
    mdns.scan = boom
    snap = asyncio.run(network_map.snapshot(FakeRegistry(FakeModule(), True), force=True))
    d = [s for s in snap["sources"] if s["id"] == "discovery"][0]
    eq("the page still loads, with a fixed sentence", d["error"], "Discovery could not listen on the network.")

with section("the route"):
    network_map.reset()
    mdns.scan = lambda ips, seconds=2.0, services=mdns.SERVICES: []
    c = _db.client(app, "viewer", base_url=ADMIN)
    r = c.get("/api/admin/network/map")
    eq("a viewer may read it", r.status_code, 200)
    check("with the merged shape", {"devices", "sources", "relay_ports", "has_switch_source"} <= set(r.json()), list(r.json()))
    member = _db.client(app, "member", base_url=ADMIN)
    eq("a member may not", member.get("/api/admin/network/map").status_code, 403)

# ------------------------------------------------------------------ names, notes and closets (the closets redesign)
SW, CAM = "aa:aa:aa:aa:aa:10", "aa:aa:aa:aa:aa:31"

with section("what is kept"):
    got = network_map.clean_stored({
        "names": {CAM: "  Wide   (back wall) ", "not a key!": "x", SW + "#4": "a name on a port", CAM + "+": ""},
        "notes": {SW + "#4": "Wall plate B4", CAM: "x" * 400},
        "places": [{"name": "Booth rack", "gear": [SW, SW, "bad key!"]}, {"name": "booth RACK", "gear": []}, {"name": " "}, "x"]})
    eq("a name is tidied; a bad key, a port key and empty text go", got["names"], {CAM: "Wide (back wall)"})
    eq("a note may be on a switch port, and is cut to length", (got["notes"][SW + "#4"], len(got["notes"][CAM])), ("Wall plate B4", 300))
    eq("a place keeps its switches once, and a second place of the same name goes", got["places"], [{"name": "Booth rack", "gear": [SW]}])
    eq("nothing saved is nothing", network_map.clean_stored(None), {"names": {}, "notes": {}, "places": []})

with section("what needs a look"):
    devs = network_map.merge([src("unifi", "UniFi", [
        {"mac": SW, "name": "Stage switch", "kind": "switch", "poe_w": 41, "poe_budget_w": 45, "model": "USW-Lite-16-PoE",
         "ports": [{"port": 5, "up": True, "speed": 100, "warn": "Linked at 100 Mbps.", "client_mac": CAM}]},
        {"mac": CAM, "ip": "10.0.0.42", "name": "Wide Shot", "uplink_mac": SW, "uplink_port": 5}])])
    found = network_map.issues(devs, [{"label": "UniFi", "on": True, "error": ""}, {"label": "Fake", "on": True, "error": "This source could not be read."}])
    eq("worst first: a source that is not answering, then the port, then PoE",
       [i["title"] for i in found], ["Fake is not answering", "Wide Shot: Stage switch port 5", "Stage switch is near its PoE limit"])
    eq("the port's own sentence is the detail, and it points at the camera and its port",
       (found[1]["detail"], found[1]["device"], found[1]["port"], found[1]["switch"]), ("Linked at 100 Mbps.", CAM, 5, SW))
    eq("model and PoE budget come through the merge", ([d for d in devs if d["mac"] == SW][0]["model"], [d for d in devs if d["mac"] == SW][0]["poe_budget_w"]),
       ("USW-Lite-16-PoE", 45.0))
    quiet = network_map.merge([src("unifi", "UniFi", [{"mac": SW, "name": "S", "kind": "switch", "poe_w": 10, "poe_budget_w": 45, "ports": []}])])
    eq("a switch well inside its budget is not listed", network_map.issues(quiet, []), [])

with section("names, notes and places through the routes"):
    OLD_NAMES = {"192.168.1.240": "Main room", "atem-b": "Overflow"}      # Blackmagic devices saved earlier in this file: named gear
    network_map.reset()                                  # discovery still finds nothing, as in "the route"
    admin = _db.client(app, "admin", base_url=ADMIN)
    viewer = _db.client(app, "viewer", base_url=ADMIN)
    fake = FakeModule()
    fake.broken = False
    eq("a viewer may not rename", viewer.post("/api/admin/network/map/labels", json={"names": {SW: "x"}}).status_code, 403)
    eq("nor move a switch", viewer.post("/api/admin/network/map/places", json={"places": []}).status_code, 403)
    eq("names must be an object", admin.post("/api/admin/network/map/labels", json={"names": ["x"]}).status_code, 400)
    eq("places must be a list", admin.post("/api/admin/network/map/places", json={"places": "x"}).status_code, 400)
    r = admin.post("/api/admin/network/map/labels", json={"names": {SW: "Booth core"}, "notes": {SW: "Top of the rack", SW + "#3": "Spare"}})
    eq("an admin renames and notes", (r.status_code, r.json()["names"], r.json()["notes"]),
       (200, {SW: "Booth core", **OLD_NAMES}, {SW: "Top of the rack", SW + "#3": "Spare"}))
    snap = asyncio.run(network_map.snapshot(FakeRegistry(fake, True), force=True))
    me = [d for d in snap["devices"] if d["mac"] == SW][0]
    eq("the map shows the name Relay was given, and keeps the device's own", (me["name"], me["own_name"]), ("Booth core", "Fake switch"))
    eq("the notes come with the map", snap["notes"][SW + "#3"], "Spare")
    again = asyncio.run(network_map.snapshot(FakeRegistry(fake, True)))
    eq("a cached read still carries the name", [d["name"] for d in again["devices"] if d["mac"] == SW], ["Booth core"])
    r = admin.post("/api/admin/network/map/labels", json={"names": {SW: ""}, "notes": {SW + "#3": "  "}})
    eq("empty text clears a name and a note; the rest stay", (r.json()["names"], r.json()["notes"]), (OLD_NAMES, {SW: "Top of the rack"}))
    r = admin.post("/api/admin/network/map/places", json={"places": [{"name": "Booth rack", "gear": [SW]}, {"name": "Lobby", "gear": []}]})
    eq("places are saved in order", r.json()["places"], [{"name": "Booth rack", "gear": [SW]}, {"name": "Lobby", "gear": []}])
    eq("a closet is a rack inside a core place of the same name", [(r["name"], r["place"]) for r in gear.racks()],
       [("Booth rack", "Booth rack"), ("Lobby", "Lobby")])
    eq("the switch is gear in that rack, under one key", (gear.by_key("mac:" + SW)["rack"] if False else gear.by_key("mac:" + SW)["rack_ref"] != ""), True)
    from app import audit
    eq("both are audited", [e["action"] for e in audit.search(kind="network")["entries"][:2]], ["network.places", "network.labels"])

with section("a switch's ports are core ports (#540), and every saved port note is still there"):
    PORTED = "aa:aa:aa:aa:aa:30"
    config.save({"network_map": {"names": {}, "notes": {PORTED + "#7": "Wall plate B7"}, "places": []}}, replace=("network_map",))

    class PortedSwitch(FakeModule):
        live = [{"port": 1, "name": "Uplink SFP+", "up": True, "speed": 10000, "uplink": True},
                {"port": 7, "name": "Port 7", "up": False, "poe_w": 0.0, "errors": 3},
                {"port": 8, "name": "Booth", "up": True, "speed": 1000, "poe_w": 4.26, "client": "Stage box", "warn": "Many errors"}]

        def network_map(self):
            return [{"mac": PORTED, "ip": "10.0.0.30", "name": "Ported switch", "kind": "switch", "ports": self.live}]

    sw = PortedSwitch()
    network_map.reset()
    snap = asyncio.run(network_map.snapshot(FakeRegistry(sw, True), force=True))
    g = gear.by_key("mac:" + PORTED)
    rows = {r["key"]: r for r in ports.of_gear(g["id"])}
    eq("each switch port is a core port, keyed by its number", sorted(rows), ["1", "7", "8"])
    eq("an SFP cage is told apart from Ethernet", (rows["1"]["kind"], rows["8"]["kind"]), ("sfp", "eth"))
    eq("the link as the switch says it", (rows["1"]["link_up"], rows["7"]["link_up"], rows["8"]["poe_w"]), (True, False, 4.3))
    eq("a note saved in config before #540 moved onto its port", (rows["7"]["note"], config.get()["network_map"]["notes"]),
       ("Wall plate B7", {}))
    eq("and still shows on the page", snap["notes"][PORTED + "#7"], "Wall plate B7")
    shown = {p["port"]: p for d in snap["devices"] if d["mac"] == PORTED for p in d["ports"]}
    eq("the map draws the kept ports with what the switch says now",
       (shown[8]["ref"], shown[8]["client"], shown[8]["warn"], shown[7]["up"]), (rows["8"]["ref"], "Stage box", "Many errors", False))
    sw.live = sw.live[:2]
    network_map.reset()
    snap = asyncio.run(network_map.snapshot(FakeRegistry(sw, True), force=True))
    gone = {p["port"]: p for d in snap["devices"] if d["mac"] == PORTED for p in d["ports"]}[8]
    eq("a port the switch stops reporting is kept, missing, with no link", (gone["missing"], gone["up"], gone["link"]), (True, False, None))
    network_map.save_labels(notes={PORTED + "#12": "Future camera"})
    eq("a note on a port the switch has not listed makes its port", ports.by_key(g["id"], "12")["note"], "Future camera")
    network_map.save_labels(notes={PORTED + "#12": ""})
    eq("empty text clears it", ports.by_key(g["id"], "12")["note"], "")

done()
