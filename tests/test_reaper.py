"""REAPER module (#413) against a fake REAPER OSC surface on localhost: OSC encoding, following REAPER's state, track
slots, record and stop, named markers, auto-record with the stream and schedule windows, the checks and alarms, core's
recording hook, the slide tag, song and stage-message markers, board widgets and the admin routes. No real REAPER.
Run: python tests/test_reaper.py
"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import time
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
from app import config  # noqa: E402
from app import modules as registry  # noqa: E402
from app.alerts import alerts  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.reaper import MODULE  # noqa: E402
from app.modules.reaper import client as cl  # noqa: E402
from app.modules.reaper import osc  # noqa: E402
from app.modules.reaper import service as svc  # noqa: E402
from app.streamstatus import streams  # noqa: E402
from _harness import await_for, check, done, eq, section  # noqa: E402


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeReaper(asyncio.DatagramProtocol):
    """Just enough of REAPER's OSC surface: transport, markers, a refresh with three tracks and empty slots."""

    def __init__(self, relay_port: int) -> None:
        self.relay = ("127.0.0.1", relay_port)
        self.got: list[tuple[str, list]] = []
        self.recording = False
        self.markers: list[list] = []          # [time, name]
        self.tracks = {1: ["Pastor", 1.0], 2: ["Band", 0.0], 3: ["Room", 0.0]}   # name, armed
        self.bank = 8
        self.vu = {1: 0.5}
        self.ignore_record = False
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def say(self, *msgs):
        for address, *args in msgs:
            self.transport.sendto(osc.message(address, *args), self.relay)

    def state(self):
        out = [("/record", 1.0 if self.recording else 0.0), ("/stop", 0.0 if self.recording else 1.0),
               ("/play", 1.0 if self.recording else 0.0), ("/time", 6.0)]
        for n in range(1, self.bank + 1):
            if n in self.tracks:
                name, armed = self.tracks[n]
                out += [(f"/track/{n}/name", name), (f"/track/{n}/recarm", armed), (f"/track/{n}/mute", 0.0),
                        (f"/track/{n}/volume/db", 0.0), (f"/track/{n}/pan2/str", "100W")]
            else:
                out += [(f"/track/{n}/name", f"Track {n}"), (f"/track/{n}/recarm", 0.0),
                        (f"/track/{n}/volume/db", -150.0), (f"/track/{n}/pan2/str", "0W")]
            if n in self.vu:
                out.append((f"/track/{n}/vu", self.vu[n]))
        return out

    def datagram_received(self, data, addr):
        for address, args in osc.parse(data):
            self.got.append((address, args))
            if address == "/device/track/count":
                self.bank = min(int(args[0]), 16)
            elif address == "/action" and args == [cl.A_REFRESH]:
                self.say(*self.state())
            elif address == "/record" and not self.ignore_record:
                self.recording = True
                self.say(("/record", 1.0), ("/play", 1.0), ("/stop", 0.0))
            elif address == "/stop":
                self.recording = False
                self.say(("/record", 0.0), ("/play", 0.0), ("/stop", 1.0))
            elif address == "/action" and args == [cl.A_MARKER]:
                self.markers.append([7.0, ""])
                self.say(("/lastmarker/number/str", str(len(self.markers))), ("/lastmarker/time", 7.0))
            elif address == "/lastmarker/name" and self.markers:
                self.markers[-1][1] = args[0]
                self.say(("/lastmarker/name", args[0]))
            elif address.endswith("/recarm"):
                n = int(address.split("/")[2])
                if n in self.tracks:
                    self.tracks[n][1] = args[0]
                    self.say((address, args[0]))

    def sent(self, address):
        return [a for a, _ in self.got if a == address]


with section("OSC"):
    m = osc.message("/x", "hi", 3, 1.5)
    eq("a message round trips", osc.parse(m), [("/x", ["hi", 3, 1.5])])
    eq("no arguments", osc.parse(osc.message("/record")), [("/record", [])])
    b = b"#bundle\0" + b"\0" * 8
    for part in (osc.message("/a", 1.0), osc.message("/b", "x")):
        b += len(part).to_bytes(4, "big") + part
    eq("a bundle is opened", [a for a, _ in osc.parse(b)], ["/a", "/b"])
    eq("rubbish is dropped", osc.parse(b"\xff\x00garbage"), [])
    eq("a cut message is dropped", osc.parse(osc.message("/x", 1.0)[:-2]), [])
    try:
        osc.message("/x", True)
        check("a bool is refused", False)
    except TypeError:
        check("a bool is refused", True)

with section("settings"):
    s = svc.clean_settings({"machines": [{"host": "127.0.0.1"}, {"host": "bad host!", "listen_port": 9001},
                                         {"silent_s": 2, "expect": ["Pastor", "pastor", "Band"]}, {}, {}]})
    eq("four at most", len(s["machines"]), 4)
    eq("defaults", {k: s["machines"][0][k] for k in ("port", "listen_port", "stop_delay", "song_markers", "check_armed")},
       {"port": 8000, "listen_port": 9001, "stop_delay": 60, "song_markers": True, "check_armed": True})
    eq("a bad address is dropped", s["machines"][1]["host"], "")
    check("each gets its own listen port", len({m["listen_port"] for m in s["machines"]}) == 4, s)
    eq("silence is at least 5 s", s["machines"][2]["silent_s"], 5)
    eq("expected tracks without repeats", s["machines"][2]["expect"], ["Pastor", "Band"])
    eq("auto-record is off until chosen", (s["machines"][0]["follow_stream"], s["machines"][0]["follow_schedule"]),
       (False, False))

with section("track slots"):
    eq("an empty slot is not a track", cl.Track(4, "Track 4", volume_db=-150.0, width="0W").real(), False)
    eq("a named track is", cl.Track(4, "Pastor", volume_db=-150.0, width="0W").real(), True)
    eq("an armed slot is", cl.Track(4, "Track 4", armed=True, volume_db=-150.0, width="0W").real(), True)
    eq("an unnamed track at unity is", cl.Track(4, "Track 4", volume_db=0.0, width="100W").real(), True)


async def live_tests():
    loop = asyncio.get_running_loop()
    relay_port, reaper_port = free_udp_port(), free_udp_port()
    tr, fake = await loop.create_datagram_endpoint(lambda: FakeReaper(relay_port), local_addr=("127.0.0.1", reaper_port))
    cfg = {"machines": [{"id": "rec1", "name": "Record PC", "host": "127.0.0.1", "port": reaper_port,
                         "listen_port": relay_port, "stop_delay": 0, "expect": ["Pastor"], "silent_s": 5}]}
    config.save({"reaper": cfg, "schedules": []})
    await svc.service.start()
    m = svc.service.machine("rec1")
    try:
        with section("connect"):
            check("REAPER answers the refresh", await await_for(lambda: m.link.reached()), m.error)
            check("the bank is raised", ("/device/track/count", [cl.DEFAULT_TRACKS]) in fake.got, fake.got[:3])
            await await_for(lambda: len(m.tracks()) == 3)
            eq("only real tracks are listed", [t["name"] for t in m.tracks()], ["Pastor", "Band", "Room"])
            eq("Pastor is armed", [t["armed"] for t in m.tracks()], [True, False, False])
            eq("idle while connected", m.health()[0], "idle")

        with section("record, marker, stop"):
            eq("record", svc.service.record(), ["Record PC"])
            check("REAPER says it is recording", await await_for(lambda: m.link.state.recording))
            check("the take's start is known", m.link.state.record_since is not None)
            eq("a second record is not sent", (svc.service.record(), fake.sent("/record").__len__()), (["Record PC"], 1))
            eq("named marker", await svc.service.marker("Sermon"), ["Record PC"])
            check("REAPER named it", await await_for(lambda: fake.markers and fake.markers[-1][1] == "Sermon"),
                  fake.markers)
            check("the page sees the marker", await await_for(lambda: m.link.state.marker_name == "Sermon"))
            eq("action", svc.service.action(40001), ["Record PC"])
            check("the action reached REAPER", await await_for(lambda: ("/action", [40001]) in fake.got))
            eq("stop", svc.service.stop_recording(), ["Record PC"])
            check("stopped", await await_for(lambda: not m.link.state.recording))

        with section("arm a track"):
            m.link.arm(2, True)
            check("Band armed", await await_for(lambda: m.link.state.tracks[2].armed))
            m.link.arm(2, False)
            check("Band disarmed", await await_for(lambda: not m.link.state.tracks[2].armed))

        with section("checks"):
            fake.tracks[1][1] = 0.0
            m.link.refresh()
            await await_for(lambda: not m.link.state.tracks[1].armed)
            probs = [p[1] for p in m.problems()]
            check("no track armed is a warning", "No track is armed to record." in probs, probs)
            check("an expected track not armed is a warning", "Pastor is not armed." in probs, probs)
            m.cfg["expect"] = ["Organ"]
            check("a missing track is named", "There is no track called Organ." in [p[1] for p in m.problems()])
            m.cfg["expect"] = ["Pastor"]
            fake.tracks[1][1] = 1.0
            m.link.refresh()
            await await_for(lambda: m.link.state.tracks[1].armed)
            m.link.state.recording, m.link.state.record_since = True, time.time() - 60
            m.link.state.tracks[1].loud_at = time.time() - 30
            check("a silent armed track while recording is a warning",
                  any("Pastor has been silent" in p[1] for p in m.problems()), m.problems())
            m.link.state.tracks[1].loud_at = time.time()
            check("a loud one is not", not m.problems(), m.problems())
            m.link.state.recording, m.link.state.record_since = False, None
            m.cfg["silent_s"] = 0
            m.link.state.heard_at = time.time() - cl.LOST_S - 1
            eq("REAPER silent after it answered is an error", m.health()[0], "error")
            m.link.refresh()
            await await_for(lambda: m.link.reached())

        with section("auto-record with the stream"):
            m.cfg["follow_stream"] = True
            svc.service.tick()
            check("nothing while off air", not fake.recording)
            streams.publish("obs", label="OBS", on_air=True, health="ok", live_since=time.time())
            svc.service.tick()
            check("on air starts it", await await_for(lambda: m.link.state.recording))
            check("started by Relay", m.ours and m.wanted)
            streams.publish("obs", label="OBS", on_air=False, health="idle")
            svc.service.tick()
            check("off air stops it (no delay)", await await_for(lambda: not m.link.state.recording))
            check("not an unexpected stop", not m.unexpected_at)

        with section("someone stops it while the service is live"):
            streams.publish("obs", label="OBS", on_air=True, health="ok", live_since=time.time())
            svc.service.tick()
            await await_for(lambda: m.link.state.recording)
            fake.say(("/record", 0.0))                              # stopped in REAPER by hand
            fake.recording = False
            await await_for(lambda: not m.link.state.recording)
            eq("it is an error", m.health()[0], "error")
            check("and says why", "not recording while the service is live" in m.health()[1], m.health())
            svc.service.tick()
            await asyncio.sleep(0.2)
            eq("it is not started again behind their back", fake.sent("/record").__len__(), 3)
            streams.publish("obs", label="OBS", on_air=False, health="idle")
            svc.service.tick()
            check("the error ends with the service", m.health()[0] != "error", m.health())

        with section("REAPER does not start"):
            fake.ignore_record = True
            streams.publish("obs", label="OBS", on_air=True, health="ok", live_since=time.time())
            svc.service.tick()
            now = time.time() + svc.START_GRACE_S + 1
            svc.service.tick(now)
            eq("an error, once it had time", m.health(now)[0], "error")
            n = len(fake.sent("/record"))
            svc.service.tick(now + 2)
            eq("record is not pressed again and again", len(fake.sent("/record")), n)
            fake.ignore_record = False
            streams.publish("obs", label="OBS", on_air=False, health="idle")
            svc.service.tick()
            streams.clear("obs")
            m.cfg["follow_stream"] = False

        with section("auto-record in a schedule window"):
            real = svc.schedule_open
            svc.schedule_open = lambda: True
            m.cfg["follow_schedule"] = True
            try:
                svc.service.tick()
                check("a window starts it", await await_for(lambda: m.link.state.recording))
                svc.schedule_open = lambda: False
                svc.service.tick()
                check("its end stops it", await await_for(lambda: not m.link.state.recording))
            finally:
                svc.schedule_open = real
                m.cfg["follow_schedule"] = False

        with section("alarm"):
            pressed = []
            real_press, real_button = alerts.press, alerts.button
            alerts.press = lambda bid, sender, level=None: pressed.append((bid, sender))
            alerts.button = lambda bid: {"id": bid, "name": bid}
            try:
                m.cfg["alert_warn"] = "booth"
                fake.tracks[1][1] = 0.0
                m.link.refresh()
                await await_for(lambda: not m.link.state.tracks[1].armed)
                svc.service.tick()
                svc.service.tick()
                eq("raised once", pressed, [("booth", "REAPER (Record PC)")])
                fake.tracks[1][1] = 1.0
                m.link.refresh()
                await await_for(lambda: m.link.state.tracks[1].armed)
                svc.service.tick()
                eq("cleared when fixed", m.alerted, "")
            finally:
                alerts.press, alerts.button = real_press, real_button
                m.cfg["alert_warn"] = ""

        with section("core's recording hook and the slide tag"):
            _modules.attach("reaper")
            from app.modules.propresenter import tagactions

            r = await registry.control_recording("record", source="a test")
            check("record through the hook", r["ok"] and await await_for(lambda: m.link.state.recording), r)
            msg = await tagactions._tag_reaper("marker=Sermon")
            eq("the tag adds a named marker", msg, "REAPER marker added.")
            check("named", await await_for(lambda: fake.markers[-1][1] == "Sermon"))
            eq("stop through the tag", await tagactions._tag_reaper("stop"), "REAPER stopped recording.")
            await await_for(lambda: not m.link.state.recording)
            for bad in ("", "dance", "action=abc"):
                try:
                    await tagactions._tag_reaper(bad)
                    check(f"tag {bad!r} refused", False)
                except RuntimeError:
                    check(f"tag {bad!r} refused", True)

        with section("song and stage-message markers"):
            svc.service.record()
            await await_for(lambda: m.link.state.recording)
            n = len(fake.markers)
            registry.announce_presentation("Amazing Grace")
            check("a marker for the new presentation", await await_for(lambda: len(fake.markers) == n + 1
                                                                       and fake.markers[-1][1] == "Amazing Grace"))
            registry.announce_presentation("Amazing Grace")
            await asyncio.sleep(0.2)
            eq("the same one again adds none", len(fake.markers), n + 1)
            from app.alerts import Message
            alerts._emit("message.sent", Message("1", "free", "Wrap up", False, "x", time.time(), 30, [], [], "info"))
            await asyncio.sleep(0.2)
            eq("stage messages are off by default", len(fake.markers), n + 1)
            m.cfg["message_markers"] = True
            alerts._emit("message.sent", Message("2", "free", "Wrap up", False, "x", time.time(), 30, [], [], "info"))
            check("a marker for a stage message", await await_for(lambda: fake.markers[-1][1] == "Message: Wrap up"),
                  fake.markers)
            svc.service.stop_recording()
            await await_for(lambda: not m.link.state.recording)

        with section("board widgets"):
            types = [w["type"] for w in MODULE.board_widgets()]
            check("record, stop, marker, action, status and meters", {"reaper.record", "reaper.stop", "reaper.marker",
                  "reaper.action", "reaper.status", "reaper.meters"} <= set(types), types)
            items = [{"id": "a", "type": "reaper.record"}, {"id": "b", "type": "reaper.status"},
                     {"id": "c", "type": "reaper.meters"}]
            d = await MODULE.board_data(items)
            eq("record lit while stopped", d["a"], {"lit": False})
            eq("status", d["b"]["lines"], ["Stopped", "Armed 1 of 3"])
            check("meters list armed tracks", d["c"]["lines"][0].startswith("Pastor"), d)
            check("no address on a board", "127.0.0.1" not in json.dumps(d))

            class Req:
                class state:
                    pass
            r = await MODULE.board_press({"type": "reaper.marker", "props": {"name": "Offering"}}, Req())
            check("a board marker", r["ok"] and await await_for(lambda: fake.markers[-1][1] == "Offering"), r)
            eq("an action button with no number", (await MODULE.board_press({"type": "reaper.action", "props": {}},
                                                                            Req()))["ok"], False)
            r = await MODULE.board_press({"type": "reaper.record", "props": {}}, Req())
            check("a board record", r["ok"] and await await_for(lambda: m.link.state.recording), r)
            eq("lit while recording", (await MODULE.board_data(items[:1]))["a"], {"lit": True})
            eq("REC pill", MODULE.pills()[0]["text"], "REC")
            await MODULE.board_press({"type": "reaper.stop", "props": {}}, Req())
            await await_for(lambda: not m.link.state.recording)

        with section("module off"):
            _modules.get("reaper").detach()
            eq("hook cleared", registry.using("reaper.recording"), None)
            check("no presentation listener", svc.service.presentation not in registry.presentation_listeners)
            check("no alert listener", svc.service.alert_event not in alerts.listeners)
            r = await registry.control_recording("record")
            eq("the hook says it is off", r, {"ok": False, "error": "The REAPER module is off."})
    finally:
        await svc.service.stop()
        tr.close()


asyncio.run(live_tests())


async def retry_test():
    with section("a taken listen port is tried again"):
        port = free_udp_port()
        hold = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        hold.bind(("127.0.0.1", port))
        svc.RETRY_S = 0.05
        m = svc.Machine(svc.clean_settings({"machines": [{"host": "127.0.0.1", "listen_port": port}]})["machines"][0])
        try:
            await m.start()
            check("says the port is taken", "Could not listen on UDP port" in m.error, m.error)
            hold.close()
            check("opens once it is free", await await_for(lambda: m.error == "" and m.link.transport is not None))
        finally:
            await m.stop()
            hold.close()


asyncio.run(retry_test())

# ------------------------------------------------------------------------------------------------ routes
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"reaper": {"enabled": True}},
             "reaper": {"machines": []}})
_modules.attach("reaper")
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", username="op1", base_url="http://relay.local:8001")
A = "/api/admin/reaper"


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr=None):
        self.sent.extend(osc.parse(data))

    def close(self):
        pass


with section("settings route"):
    r = admin.put(A + "/settings", json={"machines": [{"name": "Record PC"}]})
    eq("saved", r.status_code, 200)
    mid = r.json()["machines"][0]["id"]
    eq("a bad address is refused", admin.put(A + "/settings", json={"machines": [{"host": "not a host!"}]}).status_code, 400)
    eq("five computers are refused", admin.put(A + "/settings", json={"machines": [{}] * 5}).status_code, 400)
    eq("two on one listen port are refused", admin.put(A + "/settings", json={
        "machines": [{"listen_port": 9001}, {"listen_port": 9001}]}).status_code, 400)
    eq("a viewer cannot change it", viewer.put(A + "/settings", json={"machines": []}).status_code, 403)
    eq("a viewer can read it", viewer.get(A).status_code, 200)
    st = admin.get(A).json()["machines"][0]["state"]
    eq("no address: says so", (st["health"], st["detail"]), ("error", "Set REAPER's address."))
    check("the page gets the alert buttons to choose from", isinstance(admin.get(A).json()["alert_buttons"], list))

with section("operator routes"):
    svc.service.machines = {}
    eq("no computer answers", operator.post(A + "/record", json={}).status_code, 502)
    eq("a viewer cannot record", viewer.post(A + "/record", json={}).status_code, 403)
    mach = svc.Machine({**svc.clean_settings({"machines": [{"id": mid, "name": "Record PC", "host": "127.0.0.1"}]})
                        ["machines"][0]})
    ft = FakeTransport()
    mach.link.transport, mach.link.peer, mach.link.state.heard_at = ft, ("127.0.0.1", 8000), time.time()
    mach.link.state.tracks[2] = cl.Track(2, "Band")
    svc.service.machines = {mid: mach}
    eq("record", operator.post(A + "/record", json={"machine": mid}).json(), {"ok": True, "done": ["Record PC"]})
    eq("sent once", ft.sent[-1], ("/record", []))
    eq("no such computer", operator.post(A + "/record", json={"machine": "zz"}).status_code, 404)
    mach.link.state.recording = True
    operator.post(A + "/stop", json={})
    eq("stop", ft.sent[-1], ("/stop", []))
    mach.stop_sent_at = time.time() - svc.STOP_CONFIRM_S - 1
    check("an unconfirmed stop points at REAPER's save prompt", mach.health()[0] == "warn"
          and "prompt to save" in mach.health()[1], mach.health())
    mach.link.apply([("/record", [0.0])])
    eq("REAPER's answer clears it", mach.stop_sent_at, 0.0)
    r = operator.post(A + "/marker", json={"name": "  Sermon\n"})
    eq("marker inserted then named", ft.sent[-2:], [("/action", [cl.A_MARKER]), ("/lastmarker/name", ["Sermon"])])
    eq("an action needs a number", operator.post(A + "/action", json={"action": "x"}).status_code, 400)
    operator.post(A + "/action", json={"action": "40001"})
    eq("action", ft.sent[-1], ("/action", [40001]))
    operator.post(A + "/track", json={"machine": mid, "track": 2, "arm": True})
    eq("arm", ft.sent[-1], ("/track/2/recarm", [1.0]))
    operator.post(A + "/track", json={"machine": mid, "track": 2, "mute": False})
    eq("unmute", ft.sent[-1], ("/track/2/mute", [0.0]))
    eq("arm needs yes or no", operator.post(A + "/track", json={"machine": mid, "track": 2}).status_code, 400)
    eq("track out of range", operator.post(A + "/track", json={"machine": mid, "track": 999, "arm": True}).status_code, 400)
    eq("an operator cannot change settings", operator.put(A + "/settings", json={"machines": []}).status_code, 403)
    r = admin.post(A + "/test", json={"machine": mid})
    check("the test says REAPER did not answer", r.json()["ok"] is False and "did not answer" in r.json()["detail"],
          r.text)
    svc.service.machines = {}

with section("module off"):
    _modules.get("reaper").detach()
    eq("hook cleared", registry.using("reaper.recording"), None)

done()
