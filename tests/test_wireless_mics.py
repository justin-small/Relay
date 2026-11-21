"""Wireless mics module (#153, slice 1): Shure framing and state, a receiver against a fake Shure receiver on loopback,
settings, warnings, positions and the admin routes with the module on and off.

NO REAL SHURE RECEIVER WAS USED. The fake answers the way Shure's published command strings say, so this proves the
client against that reading of the documents, not against hardware. Run: python tests/test_wireless_mics.py
"""
import asyncio
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
from app import config, db, directory  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.wireless_mics import service as svc, shure  # noqa: E402

from _harness import await_for, check, done, eq, section  # noqa: E402

with section("Framing"):
    msgs, rest = shure.parse(b"< REP 1 BATT_CHARGE 085 >< SAMPLE 2 ALL AX 090 030 >< REP 1 AUD")
    eq("whole messages come out as words", msgs, [["REP", "1", "BATT_CHARGE", "085"], ["SAMPLE", "2", "ALL", "AX", "090", "030"]])
    eq("an unfinished message is kept", rest, b"< REP 1 AUD")
    eq("junk before a message is skipped", shure.parse(b"noise< GET 1 X >")[0], [["GET", "1", "X"]])
    eq("no message, nothing kept", shure.parse(b"abc"), ([], b""))

with section("State from messages"):
    chans = {1: shure.blank(), 2: shure.blank()}
    check("a transmitter type marks it present", shure.apply(chans, ["REP", "1", "TX_TYPE", "QLXD2"]) and chans[1]["present"])
    shure.apply(chans, ["REP", "1", "BATT_CHARGE", "085"])
    shure.apply(chans, ["REP", "1", "BATT_RUN_TIME", "00240"])
    shure.apply(chans, ["REP", "1", "AUDIO_MUTE", "ON"])
    shure.apply(chans, ["REP", "1", "CHAN_NAME", "{Pastor}"])
    eq("battery, run time, mute and name", (chans[1]["battery"], chans[1]["minutes"], chans[1]["muted"], chans[1]["name"]),
       (85, 240, True, "Pastor"))
    eq("a meter sample", shure.apply(chans, ["SAMPLE", "1", "ALL", "AX", "090", "030"]), True)
    eq("RF is n - 128 dBm, audio is 0-50", (chans[1]["rf_dbm"], chans[1]["audio"], chans[1]["antenna"]), (-38, 30, "AX"))
    eq("the same sample changes nothing", shure.apply(chans, ["SAMPLE", "1", "ALL", "AX", "090", "030"]), False)
    shure.apply(chans, ["REP", "2", "BATT_CHARGE", "255"])
    eq("255 is an unknown battery", chans[2]["battery"], None)
    eq("a channel the receiver does not have is ignored", shure.apply(chans, ["REP", "9", "AUDIO_MUTE", "ON"]), False)
    shure.apply(chans, ["REP", "1", "TX_TYPE", "UNKN"])
    eq("no transmitter clears the readings", (chans[1]["present"], chans[1]["battery"], chans[1]["rf_dbm"]), (False, None, None))
    eq("RF scales to 0-100", [shure.rf_percent(d) for d in (-120, -100, -65, -30, -10, None)], [0, 0, 50, 100, 100, None])
    eq("audio scales to 0-100", [shure.audio_percent(a) for a in (0, 25, 50, None)], [0, 50, 100, None])

with section("Settings"):
    s = svc.clean_settings({"receivers": [{"id": "a1", "name": " Stage  left ", "host": "10.0.0.5", "channels": 9},
                                          {"id": "a1", "host": "10.0.0.6"}, {"id": "A!", "host": "x"}, {"id": "b2", "host": "bad host"}],
                            "manual": [{"id": "m1", "name": "Choir", "kind": "iem"}, {"id": "m2", "name": ""}, {"id": "m3", "name": "X", "kind": "?"}],
                            "assign": {"a1:1": {"position_ref": "nonsense"}}, "low_battery": 500})
    eq("one good receiver, name tidied, channels capped to the default", [(r["id"], r["name"], r["port"], r["channels"]) for r in s["receivers"]],
       [("a1", "Stage left", 2202, 4)])
    eq("manual gear needs a name and a known kind", [(m["id"], m["kind"]) for m in s["manual"]], [("m1", "iem"), ("m3", "mic")])
    eq("an assignment that is not a position ref is dropped, a bad threshold falls back", (s["assign"], s["low_battery"]), ({}, 20))
    eq("nothing in, defaults out", svc.clean_settings(None)["receivers"], [])

with section("Warnings"):
    ok = {**shure.blank(), "present": True, "battery": 80, "rf_dbm": -50}
    eq("a healthy transmitter has none", svc.warnings_for(ok, 20, -85), [])
    eq("low battery warns", [w["level"] for w in svc.warnings_for({**ok, "battery": 20}, 20, -85)], ["warn"])
    eq("a nearly flat battery is bad", [w["level"] for w in svc.warnings_for({**ok, "battery": 10}, 20, -85)], ["bad"])
    eq("weak signal and mute", [w["id"] for w in svc.warnings_for({**ok, "rf_dbm": -90, "muted": True}, 20, -85)], ["rf", "mute"])
    eq("a card's level is its worst warning", svc.card_level([{"level": "warn"}, {"level": "bad"}]), "bad")


class FakeShure:
    """A two-channel receiver on loopback: answers GET from `values`, starts SAMPLEs after SET METER_RATE."""

    def __init__(self):
        self.seen, self.writers, self.server = [], [], None
        self.values = {(1, "TX_TYPE"): "QLXD2", (1, "BATT_CHARGE"): "012", (1, "BATT_RUN_TIME"): "00030",
                       (1, "AUDIO_MUTE"): "OFF", (1, "CHAN_NAME"): "{Pastor}", (2, "TX_TYPE"): "UNKN"}

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        self.writers.append(writer)
        buf = b""
        while not reader.at_eof():
            data = await reader.read(1024)
            if not data:
                break
            msgs, buf = shure.parse(buf + data)
            for w in msgs:
                self.seen.append(" ".join(w))
                if w[0] == "GET" and (int(w[1]), w[2]) in self.values:
                    writer.write(f"< REP {w[1]} {w[2]} {self.values[(int(w[1]), w[2])]} >".encode())
                elif w[0] == "SET" and w[2] == "METER_RATE":
                    writer.write(f"< SAMPLE {w[1]} ALL XB 060 040 >".encode())
            await writer.drain()

    def push(self, text):
        for w in self.writers:
            w.write(text.encode())

    async def stop(self):
        for w in self.writers:
            w.close()
        self.server.close()
        await self.server.wait_closed()


async def wire():
    fake = FakeShure()
    port = await fake.start()
    changes = []
    rx = shure.Receiver("a1", "Stage", "127.0.0.1", port, 2, lambda: changes.append(1))
    rx.start()
    await await_for(lambda: rx.connected and rx.chans[1]["rf_dbm"] is not None)
    out = {"asked": list(fake.seen), "ch1": dict(rx.chans[1]), "ch2": dict(rx.chans[2]), "changes": len(changes)}
    fake.push("< REP 1 BATT_CHARGE 009 >< REP 1 AUDIO_MUTE ON >")
    await await_for(lambda: rx.chans[1]["battery"] == 9)
    out["pushed"] = (rx.chans[1]["battery"], rx.chans[1]["muted"])
    await fake.stop()                                              # the receiver goes away
    await await_for(lambda: not rx.connected)
    out["after_drop"] = (rx.connected, rx.chans[1]["present"], bool(rx.error))
    await rx.close()
    return out


with section("A receiver against a fake Shure receiver on loopback"):
    got = asyncio.run(wire())
    check("it turns on meters per channel and asks each value", "SET 1 METER_RATE 00500" in got["asked"]
          and "SET 2 METER_RATE 00500" in got["asked"] and "GET 1 BATT_CHARGE" in got["asked"], got["asked"])
    eq("channel 1 is read", (got["ch1"]["name"], got["ch1"]["battery"], got["ch1"]["minutes"], got["ch1"]["rf_dbm"], got["ch1"]["audio"]),
       ("Pastor", 12, 30, -68, 40))
    eq("channel 2 has no transmitter", got["ch2"]["present"], False)
    check("the page was told about changes", got["changes"] > 0)
    eq("a pushed report changes the state with no new question", got["pushed"], (9, True))
    eq("when the receiver goes away it is offline, its readings forgotten, and it says why", got["after_drop"], (False, False, True))

with section("Positions, cards and the admin routes"):
    config.ensure_file()
    db.execute("insert into directory_items (ref, kind, provider, parent, name, seen_at) values (?, ?, ?, ?, ?, ?)",
               ("team:pco:7", "team", "pco", None, "Band", db.now()))
    db.execute("insert into directory_items (ref, kind, provider, parent, name, seen_at) values (?, ?, ?, ?, ?, ?)",
               ("position:pco:7/9", "position", "pco", "team:pco:7", "Vocals", db.now()))
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"wireless_mics": {"enabled": True}},
                 "wireless_mics": {}}, replace=("wireless_mics",))
    _modules.attach("wireless_mics")
    svc.service._running = False                      # the routes' restarts must not open real connections
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    member = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")
    W = "/api/admin/wireless-mics"

    res = admin.get(W)
    eq("empty to begin with, with the positions to pick", (res.status_code, res.json()["cards"], [p["ref"] for p in res.json()["positions"]]),
       (200, [], ["position:pco:7/9"]))
    body = {"receivers": [{"id": "a1", "name": "Stage", "host": "10.0.0.5"}], "manual": [{"id": "m1", "name": "Choir pack", "kind": "iem"}],
            "assign": {"manual:m1": {"position_ref": "position:pco:7/9"}}}
    res = admin.put(W + "/settings", json=body)
    cards = {c["key"]: c for c in res.json()["cards"]}
    eq("saved: four receiver channels and one manual pack", (res.status_code, len(cards)), (200, 5))
    eq("the manual pack shows its position by the directory's name and no readings",
       (cards["manual:m1"]["position"], cards["manual:m1"]["online"], cards["manual:m1"]["battery"], cards["manual:m1"]["kind"]),
       ("Vocals", None, None, "iem"))
    eq("a receiver Relay has not reached shows as offline and not present", (cards["a1:1"]["online"], cards["a1:1"]["present"]), (False, False))
    eq("a made-up position is refused", admin.put(W + "/settings", json={**body, "assign": {"manual:m1": {"position_ref": "position:pco:1/1"}}}).status_code, 400)
    eq("a receiver with no address is refused", admin.put(W + "/settings", json={"receivers": [{"id": "z", "host": ""}]}).status_code, 400)
    eq("a viewer can look but not change", (viewer.get(W).status_code, viewer.put(W + "/settings", json=body).status_code), (200, 403))
    eq("an operator cannot change it either", member.put(W + "/settings", json=body).status_code, 403)

    db.execute("update directory_items set name = ? where ref = ?", ("Choir", "position:pco:7/9"))
    svc.service.refresh_saved()
    eq("a renamed position is renamed in the saved assignment", config.get()["wireless_mics"]["assign"]["manual:m1"]["position_name"], "Choir")
    eq("health with a receiver that is not connected", _modules.get("wireless_mics").health().state, "warn")

    config.save({"modules": {"wireless_mics": {"enabled": False}}})
    eq("off: its route answers 404", admin.get(W).status_code, 404)

done()
