"""The Network admin page and its API (#347): ports, traffic routes, statuses, warnings, Test, Forget and the
operator-panel lock-out refusal. Ports are faked, so no second NIC is needed.
Run: .venv/bin/python tests/test_network_page.py"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(TMP, "certs")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402
from app import audit, config, netinfo, netroute, netsock, ports  # noqa: E402
from app.main import app  # noqa: E402
from app.netinfo import Port  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402
from _netfake import WIFI, port  # noqa: E402

LAN = ["192.168.1.20"]
netinfo.lan_ips = lambda: list(LAN)
CREW = port("en5", "10.0.0.5/24", "Crew LAN", mac="05", speed_mbps=1000, index=5)
DANTE = port("en6", "169.254.3.3/16", "Dante", mac="06", link_local_only=True, index=6)
OLD = port("en9", (), "Old USB adapter", mac="09", up=False)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
netroute.update([WIFI, CREW, DANTE, OLD])
netroute.remember([WIFI, CREW, DANTE, OLD])
netroute.update([WIFI, CREW, DANTE])        # the old adapter has been unplugged since

admin = _db.client(app)                                   # browsing from relay.local, not this computer
local = _db.client(app, base_url="https://127.0.0.1:8001", username="local", role="admin")
viewer = _db.client(app, role="viewer", username="vee")


def by_id(rows):
    return {r["id"]: r for r in rows}


def by_key(rows):
    return {r["key"]: r for r in rows}


def ref(p):
    """The core port ref (#540) of one of this computer's adapters."""
    return ports.by_key(netroute.relay_gear()["id"], p.id)["ref"]


with section("viewers see the page; changing it needs network.manage"):
    r = viewer.get("/api/admin/network")
    eq("a viewer reads it", r.status_code, 200)
    data = r.json()
    eq("every port Relay keeps, in the order it saw them", [p["key"] for p in data["ports"]], [WIFI.id, CREW.id, DANTE.id, OLD.id])
    eq("each is a core port, by ref", [p["id"] for p in data["ports"]], [ref(x) for x in (WIFI, CREW, DANTE, OLD)])
    gone = by_key(data["ports"])[OLD.id]
    eq("a remembered port that is gone", (gone["present"], gone["friendly_name"], gone["ipv4"]), (False, "Old USB adapter", []))
    eq("speed and link carried", (by_key(data["ports"])[CREW.id]["speed_mbps"], by_key(data["ports"])[CREW.id]["up"]), (1000, True))
    classes = by_id(data["classes"])
    eq("core's classes, owned by Relay", [classes[c]["owner"] for c in ("viewers", "crew", "discovery")], ["Relay"] * 3)
    eq("nothing routed: every class on the default", {c["status"] for c in data["classes"]}, {"default"})
    check("no free-text host field anywhere", not any(k in ("host", "admin_host") for c in data["classes"] for k in c))
    eq("a viewer cannot change a route",
       viewer.post("/api/admin/network/classes/viewers", json={"port_id": CREW.id}).status_code, 403)
    eq("nor test one", viewer.post("/api/admin/network/classes/viewers/test").status_code, 403)
    page = viewer.get("/admin").text
    check("the page and its script are on the admin page", 'id="page-network"' in page and "network.js" in page)
    check("a viewer is told it is view-only", 'id="page-network"' in page and "You can view this page but not change it" in
          page.split('id="page-network"')[1].split('class="page"')[0])

with section("routing a class"):
    r = admin.post("/api/admin/network/classes/viewers", json={"port_id": ref(CREW)})
    eq("saved", r.status_code, 200)
    eq("the answer is the new page state", by_id(r.json()["classes"])["viewers"]["port_id"], ref(CREW))
    eq("stored in config, by ref and adapter id", config.get()["network"]["routes"], {"viewers": {"port": ref(CREW), "key": CREW.id}})
    eq("the port lists its traffic", by_id(r.json()["ports"])[ref(CREW)]["classes"], ["viewers"])
    with mock.patch.object(main.serving, "groups", return_value=[]):
        eq("status ok", by_id(admin.get("/api/admin/network").json()["classes"])["viewers"]["status"], "ok")
    entries = audit.search(kind="network")["entries"]
    check("audited", entries and entries[0]["action"] == "network.route" and "Crew LAN" in entries[0]["summary"],
          entries[:1])
    r = admin.post("/api/admin/network/classes/viewers", json={"port_id": CREW.id, "policy": "strict"})
    eq("policy changed", by_id(r.json()["classes"])["viewers"]["policy"], "strict")
    eq("bad policy refused with a sentence", admin.post("/api/admin/network/classes/viewers",
       json={"port_id": CREW.id, "policy": "maybe"}).json()["error"], "Choose strict or use the default network.")
    eq("an unknown port id refused", admin.post("/api/admin/network/classes/viewers",
       json={"port_id": "mac:ff:ff:ff:ff:ff:ff"}).status_code, 409)
    eq("free text refused", admin.post("/api/admin/network/classes/viewers", json={"port_id": 7}).status_code, 400)
    eq("an unknown class refused", admin.post("/api/admin/network/classes/nosuch", json={"port_id": CREW.id}).status_code, 409)
    r = admin.post("/api/admin/network/classes/viewers", json={"port_id": None, "policy": "fallback"})
    eq("back to the default", (by_id(r.json()["classes"])["viewers"]["port_id"], config.get()["network"]["routes"]), (None, {}))

with section("statuses"):
    admin.post("/api/admin/network/classes/discovery", json={"port_id": DANTE.id, "policy": "strict"})
    netroute.update([WIFI, CREW])                     # the Dante network is unplugged
    rows = by_id(admin.get("/api/admin/network").json()["classes"])
    eq("strict and missing", rows["discovery"]["status"], "missing")
    check("with the reason", "not connected" in rows["discovery"]["detail"], rows["discovery"]["detail"])
    admin.post("/api/admin/network/classes/discovery", json={"port_id": DANTE.id, "policy": "fallback"})
    eq("fallback and missing", by_id(admin.get("/api/admin/network").json()["classes"])["discovery"]["status"], "fallback")
    netroute.update([WIFI, CREW, Port(**{**DANTE.__dict__, "up": False, "ipv4": ()})])
    admin.post("/api/admin/network/classes/discovery", json={"port_id": DANTE.id, "policy": "strict"})
    eq("strict and down", by_id(admin.get("/api/admin/network").json()["classes"])["discovery"]["status"], "down")
    netroute.update([WIFI, CREW, DANTE])
    group = type("G", (), {"traffic": "discovery", "hosts": ["169.254.9.9", "127.0.0.1"]})()
    with mock.patch.object(main.serving, "groups", return_value=[group]):
        eq("a listener still on the old address: changed",
           by_id(admin.get("/api/admin/network").json()["classes"])["discovery"]["status"], "changed")

with section("warnings"):
    twin = Port(id="mac:aa:00:00:00:00:07", name="en7", friendly_name="Second crew", ipv4=("10.0.0.9/24",), up=True)
    bare = Port(id="mac:aa:00:00:00:00:08", name="en8", friendly_name="Spare", ipv4=(), up=True, kind="ethernet")
    netroute.update([WIFI, CREW, DANTE, twin, bare])
    w = admin.get("/api/admin/network").json()["warnings"]
    check("two ports on one subnet", any("Crew LAN and Second crew are both on 10.0.0.0/24" in x for x in w), w)
    check("a class on a self-assigned-only port", any("only a self-assigned (169.254) address" in x for x in w), w)
    check("a connected port with no address", any("Spare is connected but has no address" in x for x in w), w)
    netroute.update([WIFI, CREW, DANTE])

with section("the operator panel is never moved off the address the admin is using"):
    r = admin.post("/api/admin/network/classes/crew", json={"port_id": CREW.id})
    eq("refused from a browser on another address", r.status_code, 409)
    check("it says how to do it instead", "https://127.0.0.1/admin" in r.json()["error"], r.json())
    eq("nothing saved", config.get()["network"]["routes"].get("crew"), None)
    r = admin.post("/api/admin/network/classes/crew", json={"port_id": None, "policy": "strict"})
    eq("a policy change with no route cannot lock anyone out", r.status_code, 200)
    with mock.patch.object(main, "_browsing_from", return_value="10.0.0.5"):
        eq("allowed from a browser already on the crew network",
           admin.post("/api/admin/network/classes/crew", json={"port_id": CREW.id}).status_code, 200)
    with mock.patch.object(main, "_browsing_from", return_value="10.0.0.5"):
        netroute.update([WIFI, DANTE])     # the crew adapter is gone: strict would leave only this computer
        eq("strict on a missing crew port, browsing from it: refused",
           admin.post("/api/admin/network/classes/crew", json={"port_id": CREW.id, "policy": "strict"}).status_code, 409)
        netroute.update([WIFI, CREW, DANTE])
    eq("from this computer anything goes", local.post("/api/admin/network/classes/crew", json={"port_id": DANTE.id}).status_code, 200)
    eq("the browsing address comes from the request", main._browsing_from(type("R", (), {"scope": {"server": ("10.1.1.1", 443)}})()),
       "10.1.1.1")
    local.post("/api/admin/network/classes/crew", json={"port_id": None})

with section("Test reports where the traffic leaves from"):
    r = admin.post("/api/admin/network/classes/viewers/test").json()
    check("unrouted: the default network, with the source address", r["ok"] and r["source"] and "default network" in r["detail"], r)
    eq("unrouted: no pin", r["pinned"], False)
    admin.post("/api/admin/network/classes/viewers", json={"port_id": CREW.id})
    r = admin.post("/api/admin/network/classes/viewers/test").json()   # 10.0.0.5 is not on this machine
    eq("a routed port this machine cannot use: a fixed sentence", (r["ok"], r["detail"]),
       (False, "Relay could not open a socket on Crew LAN at 10.0.0.5."))
    with mock.patch.object(netsock, "udp_socket") as fake:
        sock = fake.return_value
        sock.getsockname.return_value = ("10.0.0.5", 50000)
        r = admin.post("/api/admin/network/classes/viewers/test").json()
        eq("left from the port's address", (r["ok"], r["source"], r["detail"]), (True, "10.0.0.5", "Leaves from 10.0.0.5 on Crew LAN."))
        eq("probed a neighbour on that port's network, sending nothing", sock.connect.call_args.args[0], ("10.0.0.1", 9))
        sock.getsockname.return_value = ("192.168.1.20", 50000)
        r = admin.post("/api/admin/network/classes/viewers/test").json()
        eq("the OS sent it another way", (r["ok"], r["detail"]), (False, "Left from 192.168.1.20, not 10.0.0.5: the OS sent it another way."))
    netroute.update([WIFI, DANTE])
    admin.post("/api/admin/network/classes/viewers", json={"port_id": CREW.id, "policy": "strict"})
    r = admin.post("/api/admin/network/classes/viewers/test").json()
    eq("a missing port: its reason", (r["ok"], "not connected" in r["detail"]), (False, True))
    netroute.update([WIFI, CREW, DANTE])
    local.post("/api/admin/network/classes/viewers", json={"port_id": None, "policy": "fallback"})
    eq("an unknown class: 404", admin.post("/api/admin/network/classes/nosuch/test").status_code, 404)

with section("forgetting a port that is gone"):
    eq("a connected port cannot be forgotten",
       admin.post(f"/api/admin/network/ports/{ref(CREW)}/forget").json()["error"],
       "That network port is connected; only one that is gone can be forgotten.")
    local.post("/api/admin/network/classes/discovery", json={"port_id": OLD.id})
    OLD_REF = ref(OLD)
    eq("one carrying traffic cannot be forgotten", admin.post(f"/api/admin/network/ports/{OLD_REF}/forget").status_code, 409)
    local.post("/api/admin/network/classes/discovery", json={"port_id": None})
    r = admin.post(f"/api/admin/network/ports/{OLD_REF}/forget")
    eq("forgotten", (r.status_code, ports.by_key(netroute.relay_gear()["id"], OLD.id)), (200, None))
    eq("an unknown id", admin.post("/api/admin/network/ports/mac:zz/forget").status_code, 409)
    entries = audit.search(kind="network")["entries"]
    check("audited", any(e["action"] == "network.forget" for e in entries))

with section("a port connected but not yet remembered can be chosen"):
    fresh = Port(id="mac:aa:00:00:00:00:0f", name="en15", friendly_name="New adapter", ipv4=("10.9.0.2/24",), up=True)
    netroute.update([WIFI, CREW, DANTE, fresh])
    r = admin.post("/api/admin/network/classes/viewers", json={"port_id": fresh.id})
    eq("saved, and kept as a core port", (r.status_code, ports.by_key(netroute.relay_gear()["id"], fresh.id) is not None),
       (200, True))
    local.post("/api/admin/network/classes/viewers", json={"port_id": None})

with section("one Network page with tabs, and a module's tabs on it"):
    page = admin.get("/admin").text
    net = page.split('id="page-network"')[1].split('<div class="page"')[0]
    for tab in ("racks", "map", "ports", "computer", "find"):
        check(f"core's {tab} tab and its panel", f'data-tab="{tab}"' in net and f'data-pane="{tab}"' in net)
    check("the old map page is gone, and its hash opens the Map tab", 'id="page-network_map"' not in page and 'data-alias="network_map"' in net)
    check("one sidebar entry", page.count('href="#network"') == 1 and 'href="#network_map"' not in page)
    check("This computer keeps the ports and traffic tables", 'id="netPorts"' in net and 'id="netClasses"' in net)
    from app.modules import modules as registry
    config.save({"modules": {"unifi": {"enabled": True}}})
    try:
        page = admin.get("/admin").text
        net = page.split('id="page-network"')[1].split('<div class="page"')[0]
        eq("UniFi adds its tabs after core's, in order",
           [t for t in ("racks", "find", "unifi", "unifi_wifi", "unifi_av", "unifi_console") if f'data-tab="{t}"' in net],
           ["racks", "find", "unifi", "unifi_wifi", "unifi_av", "unifi_console"])
        check("…with their panels, and no sidebar entry of their own", 'data-pane="unifi_console"' in net and 'href="#unifi"' not in page)
        eq("a tab is not a sidebar page", [p["id"] for m in registry.admin_nav() for p in m["pages"] if p["id"].startswith("unifi")], [])
        eq("the registry lists them as tabs of the Network page", [t["id"] for t in registry.admin_tabs()["network"]],
           ["unifi", "unifi_wifi", "unifi_av", "unifi_console"])
    finally:
        config.save({"modules": {"unifi": {"enabled": False}}})

done()
