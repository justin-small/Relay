"""NDI Bridge help (#303): the machine list, the Host reachability check against a real local listener, the "Via Bridge"
tag on sources, the start-up text, and the route with its permission. Run: python tests/test_ndi_bridge.py
"""
import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.ndi import MODULE, bridge, runtime  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

with section("the machine list"):
    got = bridge.clean_machines([
        {"name": " Overflow  Campus ", "role": "host", "address": "203.0.113.5", "port": 5990},
        {"name": "overflow campus", "address": "10.0.0.9"},                 # the same name again
        {"name": "Booth", "role": "other", "address": "booth.local", "port": 99999},
        {"name": "Bad", "address": "not an address!"}, {"name": "", "address": "1.2.3.4"}, "junk"])
    eq("names and addresses are cleaned, duplicates and junk dropped", [(m["name"], m["role"], m["address"], m["port"]) for m in got],
       [("Overflow Campus", "host", "203.0.113.5", 5990), ("Booth", "join", "booth.local", 5990)])
    check("each has an id", all(len(m["id"]) >= 4 for m in got))
    eq("at most eight", len(bridge.clean_machines([{"name": f"M{i}", "address": "1.1.1.1"} for i in range(20)])), 8)
    again = bridge.clean_machines(got)
    eq("ids are kept when cleaned again", [m["id"] for m in again], [m["id"] for m in got])

with section("is the Host answering"):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    closed = free.getsockname()[1]
    free.close()
    ms = bridge.clean_machines([{"name": "Up", "role": "host", "address": "127.0.0.1", "port": port},
                                {"name": "Down", "role": "host", "address": "127.0.0.1", "port": closed},
                                {"name": "Local", "role": "join", "address": "127.0.0.1", "port": closed}])
    st = bridge.check(ms)
    eq("a listening Host answers", st[ms[0]["id"]], {"reachable": True, "error": ""})
    eq("a closed port says why", (st[ms[1]["id"]]["reachable"], bool(st[ms[1]["id"]]["error"])), (False, True))
    eq("a Join machine is not checked", ms[2]["id"] in st, False)
    srv.close()

with section("sources that came across Bridge"):
    ms = bridge.clean_machines([{"name": "Overflow", "address": "10.9.0.4"}])
    eq("matched by address", bridge.via("10.9.0.4", ms), "Overflow")
    eq("with a port", bridge.via("10.9.0.4:5961", ms), "Overflow")
    eq("another address is not", bridge.via("10.9.0.5", ms), "")
    eq("no address is not", bridge.via("", ms), "")

with section("start-up text"):
    host = bridge.start_text({"name": "Main", "role": "host", "address": "1.2.3.4", "port": 5990})
    check("a Host is told to forward the port", "5990" in " ".join(host["notes"]) and "/host" in host["windows"])
    join = bridge.start_text({"name": "Overflow", "role": "join", "address": "203.0.113.5", "port": 5991})
    cfg = json.loads(join["linux"].split("\n\n", 1)[1])
    eq("a Join's JSON points at the Host", (cfg["bridge"]["address"], cfg["bridge"]["port"], cfg["ndi"]["machinename"]), ("203.0.113.5:5991", 5991, "Overflow"))
    check("the key is a placeholder, never a real one", cfg["bridge"]["secure"] == "YOUR-ENCRYPTION-KEY")

with section("on the service and its route"):
    class Backend:
        def connect(self, name, address="", low=False):
            raise OSError("no")

    runtime.install(Backend())
    s = svc.service
    asyncio.run(s.start())
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"ndi": {"enabled": True}}, "ndi": s.settings()})
    s.merge([{"name": "SITE2 (Cam)", "machine": "SITE2", "source": "Cam", "host": "", "address": "10.9.0.4", "port": 0, "groups": [], "converter": None}])
    admin = _db.client(app)
    viewer = _db.client(app, "viewer", username="looker")
    r = admin.put("/api/admin/ndi/bridge", json={"machines": [{"name": "Site 2", "role": "join", "address": "10.9.0.4", "port": 5990}]})
    eq("it saves", (r.status_code, [m["name"] for m in r.json()["bridge"]]), (200, ["Site 2"]))
    eq("the source says it came via Bridge", [x["via_bridge"] for x in r.json()["sources"]], ["Site 2"])
    check("the snapshot carries the start-up text", "/join" in r.json()["bridge"][0]["start"]["windows"])
    eq("a machine with no address is refused", admin.put("/api/admin/ndi/bridge", json={"machines": [{"name": "x", "address": ""}]}).status_code, 400)
    eq("so is a list that is too long", admin.put("/api/admin/ndi/bridge", json={"machines": [{"name": f"M{i}", "address": "1.1.1.1"} for i in range(9)]}).status_code, 400)
    eq("and no list", admin.put("/api/admin/ndi/bridge", json={}).status_code, 400)
    eq("a viewer may not change it", viewer.put("/api/admin/ndi/bridge", json={"machines": []}).status_code, 403)
    eq("clearing it works", admin.put("/api/admin/ndi/bridge", json={"machines": []}).json()["bridge"], [])
    asyncio.run(s.stop())

done()
