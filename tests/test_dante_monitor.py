"""Dante monitoring (#258): the request layouts, a fake Dante device over real UDP sockets on loopback, problems that
raise once and clear, the history that survives a restart, and the stage alert button named Dante.
Run: .venv/bin/python tests/test_dante_monitor.py"""
import os
import socket
import struct
import sys
import tempfile
import threading
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
from app import alerts, config, db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.dante import arc, monitor as mon, service as dante  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402

db.migrate("dante", Path(__file__).resolve().parent.parent / "app" / "modules" / "dante" / "migrations")


class FakeDevice:
    """A Dante device on loopback: answers the clock, subscription and level requests from its fields."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.locked, self.leader, self.errors = True, True, 0
        self.subs = [(1, arc.SUB_OK, "Lead Vox@STAGE")]
        self.levels = [6, 20, 255]
        self.up = True
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                continue
            if not self.up or len(data) < 10:
                continue
            _, _, seq, cmd, _ = struct.unpack(">HHHHH", data[:10])
            if cmd == arc.CMD_CLOCK:
                payload = struct.pack(">BBHIH", (1 if self.leader else 0) | (2 if self.locked else 0), 0, self.errors, 1000, 0)
            elif cmd == arc.CMD_SUBSCRIPTIONS:
                payload = struct.pack(">H", len(self.subs)) + b"".join(
                    struct.pack(">HHB", ch, st, len(n.encode())) + n.encode() for ch, st, n in self.subs)
            elif cmd == arc.CMD_LEVELS:
                payload = struct.pack(">H", len(self.levels)) + bytes(self.levels)
            else:
                continue
            self.sock.sendto(arc.reply(cmd, seq, payload), addr)

    def close(self) -> None:
        self._stop.set()
        self._t.join(2)
        self.sock.close()


def record(box: FakeDevice, name="STAGE", reachable=True) -> dict:
    d = {"name": name, "key": name.lower(), "host": "", "ips": ["127.0.0.1"], "ports": {"arc": box.port}, "transmit":
         ["Lead Vox", "Pastor", "Spare"], "reachable": reachable, "clock": None, "subscriptions": None}
    if reachable:
        dante.Service.read_state(d, "127.0.0.1", box.port)
    return d


with section("the layouts read what a device sends, and refuse the rest"):
    eq("clock", arc.parse_clock(struct.pack(">BBHIH", 3, 0, 2, 500, 1)),
       {"leader": True, "locked": True, "follower_errors": 2, "latency_us": 500, "interface_errors": 1})
    eq("a short clock answer is no reading", arc.parse_clock(b"\x00\x01"), None)
    eq("a subscription list that runs off the end is no reading", arc.parse_subscriptions(struct.pack(">HHHB", 1, 1, 1, 9)), None)
    eq("levels", arc.parse_levels(struct.pack(">H", 2) + bytes([0, 30])), [0, -30])
    eq("a level count that disagrees is no reading", arc.parse_levels(struct.pack(">H", 3) + bytes([0, 30])), None)
    eq("an answer to another request is ignored", arc.body(arc.reply(arc.CMD_CLOCK, 5, b""), arc.CMD_LEVELS, 5), None)

with section("a fake device is read over real sockets"):
    box = FakeDevice()
    d = record(box)
    eq("clock", (d["clock"]["leader"], d["clock"]["locked"]), (True, True))
    eq("subscriptions", d["subscriptions"], [{"channel": 1, "source": "Lead Vox@STAGE", "status": "ok"}])
    box.up = False
    silent = {"name": "X", "key": "x", "ips": ["127.0.0.1"], "ports": {}, "transmit": [], "clock": None, "subscriptions": None}
    check("a silent device leaves no reading", arc.ask("127.0.0.1", box.port, arc.CMD_CLOCK, 9, wait=0.3) is None)
    box.up = True

with section("one alert when a subscription breaks, one clear on recovery, with the stage button"):
    m = mon.Monitor()
    m.stage = mon.stage_alert
    alerts.alerts.save({"auto_stop_s": 600, "buttons": [{"name": "Dante", "panels": [], "screens": ["Booth TV"]}]})
    bid = next(b["id"] for b in alerts.alerts.settings()["buttons"] if b["name"] == "Dante")
    m.observe([record(box)])
    eq("nothing wrong", m.current(), [])
    box.subs = [(1, 2, "Lead Vox@STAGE")]
    m.observe([record(box)])
    eq("one problem", [p["key"] for p in m.current()], ["unresolved:stage:1"])
    check("the stage alert is on", alerts.alerts.latest[bid].state == "on")
    m.observe([record(box)])
    eq("still one problem, no second alert", len(m.current()), 1)
    eq("one problem line in the history", [e["kind"] for e in m.history() if e["kind"] == "problem"], ["problem"])
    box.subs = [(1, arc.SUB_OK, "Lead Vox@STAGE")]
    m.observe([record(box)])
    eq("cleared", m.current(), [])
    check("the stage alert is off", alerts.alerts.latest[bid].state != "on")
    eq("history has the recovery", m.history()[0]["kind"], "recovered")

with section("unplugging a device raises one alert after two missed scans"):
    m = mon.Monitor()
    m.observe([record(box)])
    m.observe([])
    eq("one missed scan is not an outage", m.current(), [])
    m.observe([])
    eq("two are", [p["key"] for p in m.current()], ["offline:stage"])
    m.observe([])
    eq("still one", len(m.current()), 1)
    m.observe([record(box)])
    eq("back on the network clears it", m.current(), [])
    kinds = [e["kind"] for e in m.history()]
    check("vanished, appeared and the pair of problem lines are kept", {"vanished", "appeared", "problem", "recovered"} <= set(kinds))

with section("a device that stops answering its control port"):
    m = mon.Monitor()
    m.observe([record(box, reachable=False)])
    eq("offline", [p["key"] for p in m.current()], ["offline:stage"])

with section("clock: unlocked, errors climbing, leader changes"):
    m = mon.Monitor()
    m.observe([record(box)])
    box.errors = 3
    m.observe([record(box)])
    eq("errors that grew", [p["key"] for p in m.current()], ["clock:stage"])
    m.observe([record(box)])
    eq("errors that stopped growing clear", m.current(), [])
    box.locked = False
    m.observe([record(box)])
    eq("unlocked", [p["key"] for p in m.current()], ["clock:stage"])
    box.locked = True
    m.observe([record(box)])
    box.leader = False
    other = FakeDevice()
    m.observe([record(box), record(other, "BOOTH")])
    eq("the leader moved", [e["detail"] for e in m.history() if e["kind"] == "clock_leader"], ["clock leader changed from STAGE"])
    other.close()

with section("subscription changes name who made them"):
    m = mon.Monitor()
    m.observe([record(box)])
    box.subs = [(1, arc.SUB_OK, "Pastor@STAGE")]
    m.observe([record(box)])
    check("by someone else", "Dante Controller" in next(e for e in m.history() if e["kind"] == "subscription")["detail"])
    m.relay_wrote("stage")
    box.subs = [(1, arc.SUB_OK, "Spare@STAGE")]
    m.observe([record(box)])
    check("by Relay when routing says so", m.history()[0]["detail"].endswith("by Relay"))

with section("history survives a restart; stars too"):
    m = mon.Monitor()
    m.event("STAGE", "vanished", "test")
    m.star("STAGE", "Lead Vox", True)
    again = mon.Monitor()
    eq("a new Monitor reads the same history", again.history()[0]["kind"], "vanished")
    eq("and the stars", again.starred(), {("STAGE", "Lead Vox")})
    again.star("STAGE", "Lead Vox", False)
    eq("unstarred", again.starred(), set())

with section("levels for starred channels"):
    svc = dante.Service()
    svc.devices = [{"name": "STAGE", "key": "stage", "ips": ["127.0.0.1"], "ports": {"arc": box.port},
                    "transmit": ["Lead Vox", "Pastor", "Spare"], "host": ""}]
    dante.monitor.star("STAGE", "Pastor", True)
    eq("only the starred channel of another computer's device is watched", svc.meter_targets(), {"stage": {1}})
    svc.read_levels()
    eq("levels by channel name", [(c["channel"], c["level"]) for c in svc.levels["stage"]["channels"]], [("Pastor", -20)])
    local = [a for p in dante.netinfo.interfaces() for a in p.addresses]
    if local:
        svc.devices[0]["ips"] = [local[0]]
        eq("a device on this computer is watched whole", svc.meter_targets(), {"stage": {0, 1, 2}})
        svc.devices[0]["ips"] = ["127.0.0.1"]
    dante.monitor.star("STAGE", "Pastor", False)

with section("routes"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"dante": {"enabled": True}}})
    _modules.attach("dante")
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    dante.service.record([{"name": "STAGE", "key": "stage", "ips": ["10.9.0.2"], "ports": {}, "transmit": ["Lead Vox"],
                           "host": "", "via": False, "reachable": True}])
    star = {"device": "STAGE", "channel": "Lead Vox", "on": True}
    eq("a viewer cannot star", viewer.post("/api/admin/dante/star", json=star).status_code, 403)
    eq("an unknown channel is refused", admin.post("/api/admin/dante/star", json={**star, "channel": "No"}).status_code, 404)
    eq("a malformed body is refused", admin.post("/api/admin/dante/star", json={"device": "STAGE"}).status_code, 400)
    eq("star", admin.post("/api/admin/dante/star", json=star).json()["starred"], [["STAGE", "Lead Vox"]])
    eq("a viewer reads history", viewer.get("/api/admin/dante/history").status_code, 200)
    eq("state carries problems and levels", sorted(k for k in admin.get("/api/admin/dante").json() if k in ("problems", "levels", "leader")),
       ["leader", "levels", "problems"])
    config.save({"modules": {"dante": {"enabled": False}}})
    _modules.get("dante").detach()
    eq("history answers 404 when off", admin.get("/api/admin/dante/history").status_code, 404)

box.close()
done()
