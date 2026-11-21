"""The Signal map (#262): a pasted patch list read and joined with Dante's live routing, and its routes with Dante on
and off. Run: .venv/bin/python tests/test_signalmap.py"""
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

import app.main as main  # noqa: E402,F401
from app import config, db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app import modules as modules_pkg  # noqa: E402
from app.modules.dante import service as dante  # noqa: E402
from app.modules.signalmap import patch  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

HERE = Path(__file__).resolve().parent.parent / "app" / "modules"
db.migrate("signalmap", HERE / "signalmap" / "migrations")

with section("a patch list is read as typed"):
    rows, problems = patch.parse("CH,Name,Port,Socket,Upstream\n1,Kick,A1,S1,DT168\n\n2, Snare Top ,A2\n,,\n")
    eq("header, blank line and short row", [(r["ch"], r["name"], r["port"], r["socket"]) for r in rows],
       [("1", "Kick", "A1", "S1"), ("2", "Snare Top", "A2", "")])
    eq("a line without a name is reported", len(problems), 1)
    rows, _ = patch.parse("3\tChoir Mic Left\tB3\n4|Click/Guide|B4")
    eq("tabs and bars, no header: CH, NAME, PORT", [(r["ch"], r["name"], r["port"]) for r in rows],
       [("3", "Choir Mic Left", "B3"), ("4", "Click/Guide", "B4")])
    rows, _ = patch.parse("Name,CH\nKick,9")
    eq("a header sets the column order", (rows[0]["ch"], rows[0]["name"]), ("9", "Kick"))
    eq("nothing to read", patch.parse("  \n"), ([], []))

DEVICES = [
    {"name": "StageBox-1", "reachable": True, "transmit": ["Kick", "Snare Top", "Spare"], "subscriptions": []},
    {"name": "Bridge", "reachable": False, "transmit": ["Mains L"], "subscriptions": []},
    {"name": "Desk", "reachable": True, "transmit": [], "subscriptions": [
        {"channel": 1, "status": "ok", "source": "kick@stagebox-1"},
        {"channel": 2, "status": "unresolved", "source": "Snare Top@StageBox-1"},
        {"channel": 3, "status": "none", "source": ""}]},
]
ROWS = [{"ch": str(i), "name": n, "port": "", "socket": "", "upstream": ""}
        for i, n in enumerate(["Kick", "Snare Top", "Mains L", "Nobody", "Spare"], 1)]

with section("the join says what live routing does with each line"):
    j = patch.join(ROWS, DEVICES)
    eq("states", [r["live"]["state"] for r in j["rows"]], ["ok", "broken", "offline", "missing", "unsubscribed"])
    eq("Kick is received by the desk's channel 1", j["rows"][0]["live"]["receivers"],
       [{"device": "Desk", "channel": 1, "status": "ok", "relay": False}])
    eq("a channel can be named with its device", patch.join([dict(ROWS[0], name="kick@stagebox-1")], DEVICES)["rows"][0]["live"]["state"], "ok")
    eq("Dante channels the patch does not name", j["unpatched"], [])
    eq("an unnamed one is listed", patch.join(ROWS[:1], DEVICES)["unpatched"], ["Mains L@Bridge", "Snare Top@StageBox-1", "Spare@StageBox-1"])
    eq("with Dante off every line is unknown", {r["live"]["state"] for r in patch.join(ROWS, None)["rows"]}, {"unknown"})

with section("the routes, with Dante off then on"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"signalmap": {"enabled": True}}})
    _modules.attach("signalmap")
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    eq("empty to begin with", admin.get("/api/admin/signalmap").json()["saved"], None)
    eq("a viewer cannot save (403)", viewer.put("/api/admin/signalmap/patch", json={"text": "1,Kick"}).status_code, 403)
    eq("a list with no names is refused", admin.put("/api/admin/signalmap/patch", json={"text": ",,\n"}).status_code, 400)
    r = admin.put("/api/admin/signalmap/patch", json={"text": "1,Kick,A1\n2,Snare Top,A2\n3,Mains L"}).json()
    eq("saved, shown without Dante", (r["dante"], [x["live"]["state"] for x in r["rows"]]), (False, ["unknown"] * 3))
    eq("a viewer reads it", viewer.get("/api/admin/signalmap").json()["saved"]["text"].splitlines()[0], "1,Kick,A1")
    _modules.attach("dante")
    dante.service.devices.extend(dict(d, key=d["name"].lower()) for d in DEVICES)
    r = viewer.get("/api/admin/signalmap").json()
    eq("with Dante on the rows are joined", (r["dante"], [x["live"]["state"] for x in r["rows"]]), (True, ["ok", "broken", "offline"]))
    eq("a viewer cannot remove it (403)", viewer.delete("/api/admin/signalmap/patch").status_code, 403)
    eq("an admin removes it", admin.delete("/api/admin/signalmap/patch").json()["saved"], None)

DESK = {"desk": "d1", "name": "Avantis", "model": "avantis", "file": "show.tar.gz", "inputs": [
    {"channel": 1, "name": "Kick", "named": True, "source": {"kind": "io_port", "port": 1, "socket": 3, "label": "I/O Port 1 socket 3"}},
    {"channel": 2, "name": "2", "named": False, "source": {"kind": "local", "port": None, "socket": 2, "label": "Local XLR 2"}},
    {"channel": 3, "name": "Snare Top", "named": True, "source": {"kind": "local", "port": None, "socket": 3, "label": "Local XLR 3"}}]}

with section("a show-file patch becomes patch text"):
    text_ = patch.from_desk(DESK)
    rows, problems = patch.parse(text_)
    eq("named channels only, columns kept", [(r["ch"], r["name"], r["port"], r["socket"], r["upstream"]) for r in rows],
       [("1", "Kick", "io port 1", "3", "I/O Port 1 socket 3"), ("3", "Snare Top", "local", "3", "Local XLR 3")])
    eq("nothing to report", problems, [])

UNIFI = [{"mac": "sw", "ip": "10.0.0.2", "name": "Core switch", "kind": "switch", "ports": [{"port": 7, "name": "Stage box 1 Top"}]},
         {"mac": "c1", "ip": "10.0.0.11", "name": "box", "uplink_mac": "sw", "uplink_port": 7, "port_name": "", "speed": 1000},
         {"mac": "c2", "ip": "10.0.0.99", "name": "stranger", "uplink_mac": "sw", "uplink_port": 9}]

with section("the switch port a Dante device is plugged into"):
    devs = [dict(DEVICES[0], ips=["10.0.0.11"]), dict(DEVICES[1], ips=["10.0.0.50"]), dict(DEVICES[2], ips=["10.0.0.60"])]
    net = patch.switch_ports(devs, UNIFI)
    eq("found by IP, named from the port table", net, {"StageBox-1": {"switch": "Core switch", "port": 7, "label": "Stage box 1 Top", "speed": 1000}})
    eq("without UniFi there is no path", patch.switch_ports(devs, None), {})
    j = patch.join(ROWS, devs, net, {"10.0.0.60"})
    eq("the row carries its switch port", j["rows"][0]["live"]["via"]["port"], 7)
    eq("a device UniFi does not see has none", j["rows"][2]["live"]["via"], None)
    eq("the receiver on this computer is marked", j["rows"][0]["live"]["receivers"][0]["relay"], True)
    eq("a broken subscription stays on its own receiver", j["rows"][1]["live"]["receivers"][0]["status"], "unresolved")

with section("importing a desk's patch"):
    admin = _db.client(app, base_url="http://relay.local:8001")
    eq("without the sound desk module there is nothing to import (404)", admin.post("/api/admin/signalmap/import", json={"desk": "d1"}).status_code, 404)
    modules_pkg.provide("patch.audio_mixers", lambda: [DESK], owner="audio_mixers")
    r = admin.get("/api/admin/signalmap").json()
    eq("the desk is offered", [(d["id"], d["channels"]) for d in r["desks"]], [("d1", 2)])
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    eq("a viewer cannot import (403)", viewer.post("/api/admin/signalmap/import", json={"desk": "d1"}).status_code, 403)
    r = admin.post("/api/admin/signalmap/import", json={"desk": "d1"}).json()
    eq("the list is the desk's", [x["name"] for x in r["rows"]], ["Kick", "Snare Top"])
    modules_pkg.withdraw("patch.audio_mixers")


done()
