"""Dante routing (#259) and presets (#260): subscribe, lock, undo and apply a preset against fake Dante devices over
real UDP sockets on loopback, with the permissions and the history that names who did it.
Run: .venv/bin/python tests/test_dante_routing.py"""
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
from app import config, db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.dante import arc, monitor as mon, routing as rt, service as dante  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

db.migrate("dante", Path(__file__).resolve().parent.parent / "app" / "modules" / "dante" / "migrations")


class FakeDevice:
    """A Dante device on loopback that reads and writes its subscriptions."""

    def __init__(self, subs, refuse=False) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.subs = dict(subs)            # channel -> source
        self.refuse = refuse
        self.ignore_writes = False
        self.pages = 0                    # 0x3410 writes received
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:
                continue
            _, _, seq, cmd = struct.unpack(">HHHH", data[:8])
            if cmd == arc.CMD_SUBSCRIPTIONS:
                first = struct.unpack(">H", data[12:14])[0] - 1
                rows = [(ch, n, 10 if n else 0) for ch, n in sorted(self.subs.items())]
                page = rows[first:first + arc.PAGE]
                payload, result = arc.rx_page(page, more=first + arc.PAGE < len(rows))
                self.sock.sendto(arc.reply(cmd, seq, payload, result), addr)
            elif cmd == arc.CMD_SET_SUBSCRIPTION:
                proto = struct.unpack(">H", data[:2])[0]
                if self.refuse:
                    self.sock.sendto(struct.pack(">HHHHH", proto, 10, seq, cmd, 7), addr)
                    continue
                ch, tx_chan, tx_dev = data[13], *struct.unpack(">HH", data[14:18])
                n = f"{data[tx_chan:data.index(0, tx_chan)].decode()}@{data[tx_dev:data.index(0, tx_dev)].decode()}" if tx_chan else ""
                if not self.ignore_writes:
                    self.subs[ch] = n
                self.sock.sendto(arc.reply(cmd, seq, b"", protocol=proto), addr)

            elif cmd == arc.CMD_SET_SUBSCRIPTION_PAGE:
                # the 0x3410 page of a 2.8+ device: 0x0800 at 16, capacity and count at 18-19, 8-byte records from 20, strings by pointer
                proto = struct.unpack(">H", data[:2])[0]
                self.pages += 1
                if self.refuse or proto != arc.PROTOCOL_MODERN:
                    self.sock.sendto(struct.pack(">HHHHH", proto, 10, seq, cmd, 7), addr)
                    continue
                for i in range(data[19]):
                    rec_at = 20 + 8 * i
                    ch, tx_chan, tx_dev = data[rec_at + 1], *struct.unpack(">HH", data[rec_at + 4:rec_at + 8])
                    n = f"{data[tx_chan:data.index(0, tx_chan)].decode()}@{data[tx_dev:data.index(0, tx_dev)].decode()}" if tx_chan else ""
                    if not self.ignore_writes:
                        self.subs[ch] = n
                self.sock.sendto(arc.reply(cmd, seq, b"", protocol=proto), addr)

    def close(self) -> None:
        self._stop.set()
        self._t.join(2)
        self.sock.close()


stage = FakeDevice({1: "", 2: ""})
booth = FakeDevice({1: "Lead Vox@STAGE", 2: "Pastor@STAGE"})
pa = FakeDevice({1: "Lead Vox@STAGE"})


def rec(name, box, transmit) -> dict:
    d = {"name": name, "key": name.lower(), "host": "", "ips": ["127.0.0.1"], "ports": {"arc": box.port},
         "transmit": transmit, "reachable": True, "clock": None, "subscriptions": None, "via": False}
    dante.Service.read_state(d, "127.0.0.1", box.port)
    return d


def scan() -> None:
    dante.service.record([rec("STAGE", stage, ["Lead Vox", "Pastor"]), rec("BOOTH", booth, ["Mix"]), rec("PA", pa, [])])


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"dante": {"enabled": True}}})
_modules.attach("dante")
admin = _db.client(app, base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", username="op1", base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
SUB = "/api/admin/dante/routing/subscribe"
scan()

with section("the write packet is built and refusals are read"):
    # Packets Dante Controller sent to a real DT168 stage box on 2026-11-20 (sequence 0x39 and 0x3f), byte for byte.
    real_sub = bytes.fromhex("272900c100393010000008010001" "00ac00af") + b"\0" * (172 - 18) + b"16\0Stagebox-2-Bottom\0"
    real_clear = bytes.fromhex("272900ac003f3010000008010001" "00000000") + b"\0" * (172 - 18)
    eq("a subscribe is what Dante Controller sent", arc.subscribe_request(1, "16@Stagebox-2-Bottom", 0x39), real_sub)
    eq("a clear is what Dante Controller sent (0x3010 with no pointers, not 0x3014)", arc.subscribe_request(1, "", 0x3F), real_clear)
    eq("a write the device cannot take is not built", (arc.subscribe_request(300, "A@B", 1), arc.subscribe_request(1, "@B", 1)), (None, None))
    check("answers under the protocols Dante Controller uses are accepted", 0x2729 in arc.REPLY_PROTOCOLS and 0x2809 in arc.REPLY_PROTOCOLS)
    state, why = arc.set_subscription("127.0.0.1", stage.port, 1, "Pastor@STAGE")
    eq("a fake device accepts", (state, why), ("ok", ""))
    eq("and shows it", stage.subs[1], "Pastor@STAGE")
    eq("a clear is accepted", arc.set_subscription("127.0.0.1", stage.port, 1, ""), ("ok", ""))
    eq("and empties it", stage.subs[1], "")
    stage.refuse = True
    state, why = arc.set_subscription("127.0.0.1", stage.port, 1, "Pastor@STAGE")
    check("a refusal names Dante Domain Manager", state == "refused" and "Domain Manager" in why)
    stage.refuse = False

with section("a device on ARC 2.8 or newer is written with the 0x3410 page"):
    # Packets sent to a real ARC 2.8.12 device (Dante Virtual Soundcard) on 2026-11-21, both answered with result 1 and
    # read back with 0x3000: receive channel 2 to 01@StageBox-1-Top, then cleared.
    real_set = bytes.fromhex("2809002e000a3410" "0000" "000000000000" "0800" "0101" "00020003" "001c001f") + b"01\0StageBox-1-Top\0"
    real_clear = bytes.fromhex("2809001c000b3410" "0000" "000000000000" "0800" "0101" "00020003" "00000000")
    eq("a subscribe page is what the real device accepted", arc.subscribe_page_request(2, "01@StageBox-1-Top", 0x0A), real_set)
    eq("a clear page is what the real device accepted", arc.subscribe_page_request(2, "", 0x0B), real_clear)
    eq("a page the device cannot take is not built", (arc.subscribe_page_request(0, "A@B", 1), arc.subscribe_page_request(1, "@B", 1)), (None, None))
    eq("2.8 and newer are modern", [arc.is_modern(v) for v in ("2.8.15", "2.8.9", "2.10.0", "3.0")], [True] * 4)
    eq("older, unknown and unreadable are not", [arc.is_modern(v) for v in ("2.7.41", "", None, "x.y", "2")], [False] * 5)
    modern = FakeDevice({1: "Lead Vox@STAGE", 2: ""})
    eq("a modern write is accepted", arc.set_subscription("127.0.0.1", modern.port, 2, "Pastor@STAGE", modern=True), ("ok", ""))
    eq("and shows on the device", (modern.subs[2], modern.pages), ("Pastor@STAGE", 1))
    eq("a modern clear is accepted", arc.set_subscription("127.0.0.1", modern.port, 2, "", modern=True), ("ok", ""))
    eq("and empties it", modern.subs[2], "")
    modern.refuse = True
    check("a modern refusal names Dante Domain Manager", "Domain Manager" in arc.set_subscription("127.0.0.1", modern.port, 2, "Pastor@STAGE", modern=True)[1])
    modern.refuse = False
    d = rec("VIA", modern, [])
    d["arc_version"] = "2.8.15"
    dante.service.record([rec("STAGE", stage, ["Lead Vox", "Pastor"]), d])
    sent = modern.pages
    r = operator.post(SUB, json={"device": "VIA", "channel": 2, "source": "Pastor@STAGE", "confirm": True})
    eq("Relay subscribes a 2.8 device and reads it back", (r.status_code, r.json()["result"]["result"]), (200, "ok"))
    eq("with a page, not the old packet", (modern.subs[2], modern.pages - sent), ("Pastor@STAGE", 1))
    eq("and undo clears it the same way", operator.post("/api/admin/dante/routing/undo", json={"confirm": True}).status_code, 200)
    eq("so the channel is empty again", modern.subs[2], "")
    d["arc_version"] = "2.7.41"
    modern.ignore_writes = False
    dante.service.record([rec("STAGE", stage, ["Lead Vox", "Pastor"]), d])
    before = modern.pages
    operator.post(SUB, json={"device": "VIA", "channel": 2, "source": "Pastor@STAGE", "confirm": True})
    eq("a 2.7 device is not sent a page", modern.pages, before)
    modern.close()
    scan()

with section("permissions: a viewer reads, only an operator changes"):
    eq("a viewer reads the matrix", viewer.get("/api/admin/dante/routing").status_code, 200)
    eq("a viewer's write is refused (403)", viewer.post(SUB, json={"device": "PA", "channel": 1, "source": "", "confirm": True}).status_code, 403)
    eq("nothing changed", pa.subs[1], "Lead Vox@STAGE")
    eq("an operator cannot lock", operator.post("/api/admin/dante/routing/lock", json={"device": "PA", "channel": 1, "on": True}).status_code, 403)
    eq("an operator cannot save a preset", operator.post("/api/admin/dante/presets", json={"name": "x"}).status_code, 403)

with section("subscribe asks for confirmation, writes, reads back, names who"):
    body = {"device": "BOOTH", "channel": 1, "source": "Pastor@STAGE"}
    eq("no confirm, no write", operator.post(SUB, json=body).status_code, 400)
    eq("untouched", booth.subs[1], "Lead Vox@STAGE")
    eq("a source Relay never saw is refused", operator.post(SUB, json={**body, "source": "Nope@STAGE", "confirm": True}).status_code, 409)
    r = operator.post(SUB, json={**body, "confirm": True})
    eq("confirmed", r.status_code, 200)
    eq("the result names what it replaced", (r.json()["result"]["result"], r.json()["result"]["from"]), ("ok", "Lead Vox@STAGE"))
    eq("the device has it", booth.subs[1], "Pastor@STAGE")
    check("history names the operator", any("by " in e["detail"] and e["kind"] == "routing" for e in mon.monitor.history()))
    eq("undo is offered", operator.get("/api/admin/dante/routing").json()["undo"]["count"], 1)

with section("undo restores the previous subscription"):
    eq("undo needs confirm", operator.post("/api/admin/dante/routing/undo", json={}).status_code, 400)
    r = operator.post("/api/admin/dante/routing/undo", json={"confirm": True})
    eq("undone", (r.status_code, booth.subs[1]), (200, "Lead Vox@STAGE"))
    eq("nothing left to undo", r.json()["undo"], None)
    eq("a second undo is refused", operator.post("/api/admin/dante/routing/undo", json={"confirm": True}).status_code, 409)

with section("a locked channel is never changed"):
    eq("admin locks PA channel 1", admin.post("/api/admin/dante/routing/lock", json={"device": "PA", "channel": 1, "on": True}).status_code, 200)
    r = operator.post(SUB, json={"device": "PA", "channel": 1, "source": "", "confirm": True})
    eq("refused", (r.status_code, pa.subs[1]), (409, "Lead Vox@STAGE"))
    eq("the lock shows in the matrix", operator.get("/api/admin/dante/routing").json()["locks"], [["PA", 1]])

with section("a device that refuses, or says nothing, is reported plainly"):
    booth.refuse = True
    r = operator.post(SUB, json={"device": "BOOTH", "channel": 2, "source": "", "confirm": True}).json()["result"]
    check("refused", r["result"] == "refused" and "Domain Manager" in r["message"])
    booth.refuse = False
    booth.ignore_writes = True
    r = operator.post(SUB, json={"device": "BOOTH", "channel": 2, "source": "", "confirm": True}).json()["result"]
    eq("a change the device did not show is unconfirmed", r["result"], "unconfirmed")
    booth.ignore_writes = False
    check("a failed change leaves nothing to undo", operator.get("/api/admin/dante/routing").json()["undo"] is None)

with section("presets: save, break two routes, compare, apply, undo"):
    scan()
    saved = admin.post("/api/admin/dante/presets", json={"name": "Sunday"})
    eq("saved", (saved.status_code, saved.json()["saved"]["count"]), (200, 5))
    pid = saved.json()["saved"]["id"]
    eq("nothing differs yet", operator.get(f"/api/admin/dante/presets/{pid}/compare").json()["changes"], [])
    booth.subs[1], booth.subs[2] = "", "Lead Vox@STAGE"      # two routes broken
    pa.subs[1] = ""                                          # and a locked one
    scan()
    c = viewer.get(f"/api/admin/dante/presets/{pid}/compare").json()
    eq("the diff names both routes", sorted((e["device"], e["channel"], e["from"], e["to"]) for e in c["changes"]),
       [("BOOTH", 1, "", "Lead Vox@STAGE"), ("BOOTH", 2, "Lead Vox@STAGE", "Pastor@STAGE")])
    eq("the locked one is listed, not changed", [(e["device"], e["channel"]) for e in c["locked"]], [("PA", 1)])
    eq("a viewer cannot apply (403)", viewer.post(f"/api/admin/dante/presets/{pid}/apply", json={"confirm": True}).status_code, 403)
    eq("apply needs confirm", operator.post(f"/api/admin/dante/presets/{pid}/apply", json={}).status_code, 400)
    r = operator.post(f"/api/admin/dante/presets/{pid}/apply", json={"confirm": True}).json()
    eq("each result reported", sorted((x["device"], x["channel"], x["result"]) for x in r["results"]),
       [("BOOTH", 1, "ok"), ("BOOTH", 2, "ok")])
    eq("restored", (booth.subs[1], booth.subs[2], pa.subs[1]), ("Lead Vox@STAGE", "Pastor@STAGE", ""))
    eq("the locked channel stayed as it was", r["locked"][0]["device"], "PA")
    eq("undo covers the whole apply", r["undo"]["count"], 2)
    operator.post("/api/admin/dante/routing/undo", json={"confirm": True})
    eq("undo puts the broken routing back", (booth.subs[1], booth.subs[2]), ("", "Lead Vox@STAGE"))

with section("a missing device or source is listed and skipped"):
    booth.subs[1], booth.subs[2] = "Lead Vox@STAGE", "Pastor@STAGE"
    dante.service.record([rec("STAGE", stage, ["Pastor"]), rec("PA", pa, [])])    # BOOTH gone, Lead Vox gone
    c = admin.get(f"/api/admin/dante/presets/{pid}/compare").json()
    check("missing entries carry a reason", c["missing"] and all(e["why"] for e in c["missing"]))
    r = operator.post(f"/api/admin/dante/presets/{pid}/apply", json={"confirm": True}).json()
    eq("nothing was written to the missing device", (booth.subs[1], booth.subs[2]), ("Lead Vox@STAGE", "Pastor@STAGE"))
    check("they are reported", r["missing"])
    eq("a preset can be deleted by an admin", admin.delete(f"/api/admin/dante/presets/{pid}").json()["presets"], [])
    eq("a bad preset name is refused", admin.post("/api/admin/dante/presets", json={"name": ""}).status_code, 400)

config.save({"modules": {"dante": {"enabled": False}}})
_modules.get("dante").detach()
eq("routing answers 404 when off", admin.get("/api/admin/dante/routing").status_code, 404)

for b in (stage, booth, pa):
    b.close()
done()
