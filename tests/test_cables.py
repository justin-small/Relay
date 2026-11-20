"""Core cables (#541): a cable joins any two ports, an operator's can be removed, a module's reported one cannot, and the
Network page and the Blackmagic page list the same cables."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import cables, config, gear, network_map, ports  # noqa: E402
from app.main import app  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
admin = _db.client(app, base_url="http://relay.local:8001")


def refused(fn, *args, **kw) -> str:
    try:
        fn(*args, **kw)
    except cables.Refused as exc:
        return exc.key
    return ""


SW_MAC, DEV_MAC = "aa:bb:cc:00:00:51", "aa:bb:cc:00:00:52"
SWITCH = gear.adopt(["mac:" + SW_MAC], name="Booth switch", kind="switch")["id"]
ATEM = gear.adopt(["blackmagic:main"], name="ATEM", kind="atem")["id"]
HUB = gear.adopt(["blackmagic:hub"], name="Videohub", kind="videohub")["id"]
ports.sync(SWITCH, [{"key": "1", "kind": "eth"}, {"key": "2", "kind": "eth"}, {"key": "9", "kind": "eth", "gear_name": "Uplink"}])
ports.sync(ATEM, [{"key": "out1", "kind": "sdi", "direction": "out"}, {"key": "eth", "kind": "eth"}])
ports.sync(HUB, [{"key": "in1", "kind": "sdi", "direction": "in"}])
P = {(g, k): ports.by_key(g, k)["id"] for g, k in ((SWITCH, "1"), (SWITCH, "2"), (SWITCH, "9"), (ATEM, "out1"), (ATEM, "eth"), (HUB, "in1"))}

with section("refs"):
    eq("round trip", cables.cable_id(cables.ref(4)), 4)
    eq("a port ref is not a cable ref", cables.cable_id(ports.ref(4)), None)
    eq("another provider is not a relay cable", cables.cable_id("cable:pco:4"), None)

with section("a cable joins any two ports on any gear"):
    sdi = cables.add(P[(ATEM, "out1")], P[(HUB, "in1")], note="  Program   out ")
    eq("SDI between two devices", (sdi["a"]["key"], sdi["b"]["key"], sdi["by"], sdi["reported"], sdi["note"]),
       ("out1", "in1", "operator", False, "Program out"))
    net = cables.add(P[(ATEM, "eth")], P[(SWITCH, "1")])
    eq("a switch port to a device's network port", (net["a"]["gear"], net["b"]["gear"]), (ATEM, SWITCH))
    eq("the same two ports the other way round are one cable",
       refused(cables.add, P[(SWITCH, "1")], P[(ATEM, "eth")]), "exists")
    eq("a port is not cabled to itself", refused(cables.add, P[(HUB, "in1")], P[(HUB, "in1")]), "same")
    eq("a port that is not there", refused(cables.add, P[(HUB, "in1")], 99999), "no_port")
    eq("a port can have more than one cable", len(cables.of_gears([HUB])), 1)
    cables.add(P[(HUB, "in1")], P[(SWITCH, "2")])
    eq("a patch panel and a split are possible", len(cables.of_gears([HUB])), 2)
    eq("the cable between two ports is found either way", cables.between(P[(HUB, "in1")], P[(ATEM, "out1")])["id"], sdi["id"])

with section("a note goes on any cable; only an operator's cable is removed"):
    cables.set_note(net["id"], "Patch 4")
    eq("a note", cables.get(net["id"])["note"], "Patch 4")
    eq("a note on a cable that is not there", refused(cables.set_note, 9999, "x"), "no_cable")
    cables.remove(net["id"])
    eq("an operator's cable is removed", cables.get(net["id"]), None)
    eq("and again", refused(cables.remove, net["id"]), "no_cable")

with section("a module's reported cable"):
    got = cables.sync("unifi", [(P[(SWITCH, "9")], P[(ATEM, "eth")])])
    eq("is recorded as reported", [(c["by"], c["reported"], c["missing"]) for c in got], [("unifi", True, False)])
    rep = got[0]["id"]
    eq("an operator cannot remove it", refused(cables.remove, rep), "reported")
    cables.set_note(rep, "Wall plate B3")
    eq("but can annotate it", cables.get(rep)["note"], "Wall plate B3")
    cables.sync("unifi", [(P[(SWITCH, "9")], P[(ATEM, "eth")])])
    eq("reporting it again changes nothing", [c["id"] for c in cables.listing() if c["by"] == "unifi"], [rep])
    cables.sync("unifi", [])
    eq("a cable no longer reported is marked missing, not dropped", (cables.get(rep)["missing"], cables.get(rep)["note"]),
       (True, "Wall plate B3"))
    cables.sync("unifi", [(P[(ATEM, "eth")], P[(SWITCH, "9")])])
    eq("and comes back when it is reported again", cables.get(rep)["missing"], False)
    cables.sync("unifi", [(P[(HUB, "in1")], P[(SWITCH, "2")])])
    eq("a pair an operator already cabled stays theirs", (cables.between(P[(HUB, "in1")], P[(SWITCH, "2")])["by"]), "operator")

with section("deleting a port removes its cables"):
    port_id = ports.adopt(HUB, "in2", kind="sdi")["id"]
    made = cables.add(port_id, P[(SWITCH, "2")])
    ports.forget(port_id, gone=True)
    eq("the cable goes with the port", cables.get(made["id"]), None)

with section("the Network page's API"):
    res = admin.get("/api/admin/network/cables")
    body = res.json()
    eq("lists the cables and the gear with ports", (res.status_code, len(body["cables"]) >= 3, {g["name"] for g in body["gear"]}),
       (200, True, {"Booth switch", "ATEM", "Videohub"}))
    a, b = ports.ref(P[(ATEM, "eth")]), ports.ref(P[(SWITCH, "1")])
    res = admin.post("/api/admin/network/cables", json={"a": a, "b": b, "note": "Main"})
    eq("an operator adds one", (res.status_code, any(c["note"] == "Main" for c in res.json()["cables"])), (200, True))
    eq("the same one again is a conflict", admin.post("/api/admin/network/cables", json={"a": b, "b": a}).status_code, 409)
    eq("a port that is not a ref", admin.post("/api/admin/network/cables", json={"a": "nope", "b": a}).status_code, 400)
    mine = next(c for c in res.json()["cables"] if c["note"] == "Main")
    reported = next(c for c in res.json()["cables"] if c["reported"])
    eq("an operator's cable is removed", admin.delete(f"/api/admin/network/cables/{mine['id']}").status_code, 200)
    res = admin.delete(f"/api/admin/network/cables/{reported['id']}")
    eq("a reported one is refused, with the reason", (res.status_code, "reported by a module" in res.json()["error"]), (409, True))
    res = admin.post(f"/api/admin/network/cables/{reported['id']}/note", json={"text": "Rack 2"})
    eq("it takes a note", (res.status_code, next(c for c in res.json()["cables"] if c["id"] == reported["id"])["note"]), (200, "Rack 2"))
    eq("a note that is too long", admin.post(f"/api/admin/network/cables/{reported['id']}/note", json={"text": "x" * 301}).status_code, 400)
    eq("a cable that is not there", admin.delete("/api/admin/network/cables/9999").status_code, 404)

with section("UniFi's switch ports become reported cables, on the map's own devices"):
    def d(**kw):
        return {"mac": "", "ips": [], "host": "", "name": "", "kind": "client", "ndi": None, "dante": None, "model": "",
                "uplink_mac": "", "uplink_port": None, "parent": "", "seen_by": ["unifi"], "stale_by": [], "ports": [], **kw}
    sw = d(id=SW_MAC, mac=SW_MAC, name="Booth switch", kind="switch", ports=[{"port": 5, "name": "Cam", "up": True, "uplink": False}])
    cam = d(id=DEV_MAC, mac=DEV_MAC, name="Camera PC", uplink_mac=SW_MAC, uplink_port=5, parent=SW_MAC)
    wifi = d(id="aa:bb:cc:00:00:53", mac="aa:bb:cc:00:00:53", name="Phone", uplink_mac=SW_MAC, parent=SW_MAC)
    network_map.adopt_devices([sw, cam, wifi], ["unifi"])
    rows = [c for c in network_map.cable_rows() if c["by"] == "unifi" and c["a"]["key"] == "5"]
    eq("the switch port to the device's network port", [(c["a"]["gear"], c["b"]["gear"], c["b"]["key"], c["reported"]) for c in rows],
       [("Booth switch", "Camera PC", "eth", True)])
    eq("the page knows both ends by map id", (rows[0]["a"]["device"], rows[0]["b"]["device"]), (SW_MAC, DEV_MAC))
    eq("a device with no port is not cabled", [c for c in network_map.cable_rows() if c["b"]["gear"] == "Phone"], [])
    cam["uplink_port"] = None
    network_map.adopt_devices([sw, cam, wifi], ["unifi"])
    eq("a device that moves off the port leaves a missing cable", [c["missing"] for c in network_map.cable_rows()
                                                                   if c["a"]["key"] == "5"], [True])
    network_map.adopt_devices([sw, cam, wifi], [])
    eq("a source that is off does not mark its cables", [c["missing"] for c in network_map.cable_rows() if c["a"]["key"] == "5"], [True])

done()
