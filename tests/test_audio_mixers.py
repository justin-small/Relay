"""Audio Mixers (#150) against fake desks on localhost: the Allen & Heath, SQ, Behringer and Yamaha codecs, each driver
following its desk and sending commands, the desks as core gear and channels, a rename on the Network page reaching the
desk, sends as core routes, the watchdog's notification, the Planning Center name push, board widgets (the fader bank
too) and the admin routes' permissions. No real desk: the fakes speak what the published protocols say.
Run: python tests/test_audio_mixers.py
"""
import asyncio
import os
import socket
import struct
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
from app import channels, config, gearfeed, routing  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app import modules as registry  # noqa: E402
from app.modules.audio_mixers import MODULE, strip_list  # noqa: E402
from app.modules.audio_mixers import ah, behringer as bh, osc, yamaha  # noqa: E402
from app.modules.audio_mixers import drivers as drv  # noqa: E402
from app.modules.audio_mixers import service as svc  # noqa: E402
from app.modules.audio_mixers.mirror import Mirror  # noqa: E402
from app.modules.audio_mixers.models import MODELS, OFF_DB, db_to_pos, pos_to_db  # noqa: E402
from _harness import await_for, check, done, eq, section  # noqa: E402

# -- codecs -------------------------------------------------------------------------------------------------------------
with section("Allen & Heath fader law and addresses"):
    eq("0 dB is 0x6B", ah.ah_level(0.0), 0x6B)
    eq("+10 dB is the top", ah.ah_level(10.0), 0x7F)
    eq("-10 dB", ah.ah_level(-10.0), 0x57)
    eq("-inf", ah.ah_level(-90.0), 0)
    eq("back to dB", ah.ah_db(0x57), -10.0)
    eq("0 reads as off", ah.ah_db(0), OFF_DB)
    av = ah.AH(MODELS["avantis"], 1)
    eq("Avantis input 3", av.address("in", 3), ah.Address(0, 2))
    eq("Avantis mix 2 on the aux channel", av.address("mix", 2), ah.Address(2, 1))
    eq("Avantis main", av.address("main", 1), ah.Address(4, 0x30))
    eq("Avantis DCA 1", av.address("dca", 1), ah.Address(4, 0x36))
    eq("mute bytes", av.mute(av.address("in", 1), True), bytes([0x90, 0, 0x7F, 0x90, 0, 0]))
    eq("fader NRPN", av.fader(av.address("in", 1), 0.0), bytes([0xB0, 0x63, 0, 0xB0, 0x62, 0x17, 0xB0, 0x06, 0x6B]))
    eq("scene 130 is bank 1 program 1", av.scene(130), bytes([0xB0, 0, 1, 0xC0, 1]))
    eq("a name answer", av.sysex(ah.SYSEX + bytes([0, 0x02, 4]) + b"Pastor\xF7"), ("name", "in5", "Pastor"))
    eq("a colour answer", av.sysex(ah.SYSEX + bytes([2, 0x05, 0, 4, 0xF7])), ("colour", "mix1", "blue"))
    eq("a send is SysEx 0D", av.send(1, 2, 0.0), ah.SYSEX + bytes([0, 0x0D, 0, 2, 1, 0x6B, 0xF7]))
    gld = ah.AH(MODELS["gld"], 3)
    eq("GLD input 1 is 0x20 on its one channel", gld.address("in", 1), ah.Address(2, 0x20))
    eq("GLD has no main here", gld.address("main", 1), None)
    qu = ah.AH(MODELS["qu16"], 1)
    eq("Qu's SysEx header", qu.ask_name(qu.address("in", 1))[:8], ah.SYSEX_QU)
    eq("Qu LR", qu.address("main", 1), ah.Address(0, 0x67))
    eq("Qu has no sends here", qu.send(1, 1, 0.0), None)

with section("the MIDI stream"):
    p = ah.MidiParser()
    got = list(p.feed(bytes([0x90, 1, 0x7F, 1])))      # running status, the second note cut short
    eq("one note so far", got, [("note", 0, 1, 0x7F)])
    eq("the rest arrives", list(p.feed(bytes([0x00, 0xF0, 0x01]))), [("note", 0, 1, 0)])
    eq("SysEx across reads", list(p.feed(bytes([0x02, 0xF7, 0xC0, 5]))), [("sysex", bytes([0xF0, 1, 2, 0xF7])), ("pc", 0, 5)])
    eq("a new status cuts a message short", list(p.feed(bytes([0xB0, 0x63, 0x90, 2, 0x3F]))), [("note", 0, 2, 0x3F)])
    eq("real-time bytes are skipped", list(p.feed(bytes([0xB1, 0xF8, 0x06, 0x10]))), [("cc", 1, 6, 0x10)])

with section("SQ"):
    sq = ah.SQ(MODELS["sq5"], 1)
    eq("0 dB on the linear taper", ah.sq_level(0.0), (15196 >> 7, 15196 & 0x7F))
    eq("and back", ah.sq_db(*ah.sq_level(-10.0)), -10.0)
    eq("input 1 mute on", sq.mute("in", 1, True), bytes([0xB0, 0x63, 0, 0xB0, 0x62, 0, 0xB0, 0x06, 0, 0xB0, 0x26, 1]))
    eq("a mix mute is read back", sq.value(0x00, 0x45 + 1, 0, 1), ("mute", "mix2", True))
    eq("input 2's level", sq.value(*ah._param(ah.SQ_LEVEL["in"], 1), *ah.sq_level(-5.0)), ("level", "in2", -5.0))
    off = (2 - 1) * ah.SQ_MIXES + (3 - 1)
    eq("input 2's send to mix 3", sq.value(*ah._param(ah.SQ_SEND_LEVEL, off), *ah.sq_level(0.0)), ("send", "in2", (3, None, 0.0)))
    check("asks for everything", len(sq.ask_all()) == 9 * (4 * 0 + (48 + 12 + 1 + 8) * 2 + 48 * 12 * 2))

with section("AH-Net frames"):
    check("hello starts the session", ah.ahnet_hello().startswith(bytes([0xE0, 0x00, 0x04, 0x01, 0x03])))
    eq("the answer", ah.ahnet_hello_ok(bytes([0xE0, 0, 4, 2, 3, 9, 9, 0xE7])), True)
    frame = bytes([0xF0, 0, 1, 0x20, 0x02, 0x00, 0x28, 0x10, 0x00, 0x00, 0x02, 0x76, 0x00, 0xF7])
    eq("input 3 at -10 dB", ah.ahnet_fader(frame), (3, -10.0))
    name = bytes.fromhex("f00001000a000c00020148") + b"Sunday AM\x00\xf7"
    eq("a scene name", ah.ahnet_scene_name(name), "Sunday AM")
    buf = bytearray(bytes([0xE0, 0, 4, 2, 3, 1, 1, 0xE7]) + frame[:5])
    eq("a session frame, then wait", list(ah.ahnet_frames(buf)), [bytes([0xE0, 0, 4, 2, 3, 1, 1, 0xE7])])
    buf += frame[5:]
    eq("the object frame completes", list(ah.ahnet_frames(buf)), [frame])

with section("X32 and WING addresses"):
    eq("a channel fader", bh.x32_strip("/ch/03/mix/fader"), ("in3", "fader"))
    eq("a name", bh.x32_strip("/ch/03/config/name"), ("in3", "name"))
    eq("the main", bh.x32_strip("/main/st/mix/on"), ("main1", "on"))
    eq("a DCA", bh.x32_strip("/dca/2/fader"), ("dca2", "fader"))
    eq("not a DCA mix", bh.x32_strip("/dca/2/mix/fader"), None)
    eq("a send", bh.x32_send("/ch/04/mix/02/level"), (4, 2, "level"))
    eq("the fader law: 0.75 is 0 dB", bh.x32_db(0.75), 0.0)
    eq("and back", bh.x32_level(-10.0), 0.5)
    blob = struct.pack("<i", 2) + struct.pack("<2f", 1.0, 0.0)
    eq("meters in dBFS", bh.x32_meters(blob), [0.0, None])
    eq("WING ,sff", bh.wing_number(["-10.0", 0.5, -10.0]), -10.0)
    eq("WING one value", bh.wing_number([1]), 1.0)
    eq("WING text", bh.wing_number(["-oo"]), OFF_DB)
    eq("WING scenes", bh.wing_scenes(["$scenes list [Walk in, Worship ,Sermon]"]), ["Walk in", "Worship", "Sermon"])
    eq("OSC blob round trip", osc.parse(osc.message("/meters/0", b"\x01\x02\x03"))[0], ("/meters/0", [b"\x01\x02\x03"]))

with section("Yamaha RCP lines"):
    eq("a notify", yamaha.parse('NOTIFY set MIXER:Current/InCh/Fader/Level 4 0 -1000 "-10.00"'),
       ("strip", "in5", "level", -10.0))
    eq("on means unmuted", yamaha.parse("OK get MIXER:Current/St/Fader/On 0 0 1"), ("strip", "main1", "on", True))
    eq("a quoted name", yamaha.parse('OK get MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"'),
       ("strip", "in1", "name", "Lead Vox"))
    eq("a colour", yamaha.parse('OK get MIXER:Current/Mix/Label/Color 1 0 "SkyBlue"'), ("strip", "mix2", "colour", "cyan"))
    eq("-inf", yamaha.parse("OK get MIXER:Current/InCh/Fader/Level 0 0 -32768")[3], OFF_DB)
    eq("a send", yamaha.parse("NOTIFY set MIXER:Current/InCh/ToMix/On 2 3 1"), ("send", 3, 4, "on", True))
    eq("the scene", yamaha.parse("NOTIFY sscurrent_ex scene_a 12"), ("scene", 12))
    eq("a scene title", yamaha.parse('OK ssinfo_ex scene_a 12 "Sermon" "" 0'), ("scene_name", 12, "Sermon"))
    eq("the set line", yamaha.set_cmd("in", 2, "Label/Name", 'A "b"'), 'set MIXER:Current/InCh/Label/Name 1 0 "A b"\n')
    eq("CL scenes", yamaha.library(MODELS["cl5"]), "scene_a")

with section("the fader law and strip lists"):
    for db in (10.0, 0.0, -10.0, -30.0, -60.0):
        eq(f"{db} dB round trip", pos_to_db(db_to_pos(db)), db)
    eq("bottom is off", pos_to_db(0.0), OFF_DB)
    eq("a list", strip_list("main1, in1-3, mix9, nope", MODELS["x32"]), ["main1", "in1", "in2", "in3", "mix9"])
    eq("only what the desk has", strip_list("in30-40", MODELS["x32"]), ["in30", "in31", "in32"])

with section("settings are cleaned"):
    s = svc.clean_settings({"desks": [{"model": "x32", "host": "10.0.0.5", "name": "FOH"},
                                      {"model": "nope"}, {"model": "avantis", "ahnet": True, "midi_channel": 99}],
                            "watchdog": {"on": True, "user_id": 3, "mutes": ["desk1:main1", "desk1:in99", "x:in1"]}})
    eq("unknown models dropped", [d["model"] for d in s["desks"]], ["x32", "avantis"])
    eq("AH-Net only on Avantis", s["desks"][1]["ahnet"], True)
    eq("a bad MIDI channel", s["desks"][1]["midi_channel"], 1)
    eq("watched mutes on strips the desk has", s["watchdog"]["mutes"], ["desk1:main1"])
    eq("an address is checked", svc.clean_settings({"desks": [{"model": "x32", "host": "a b"}]})["desks"][0]["host"], "")


# -- fake desks ---------------------------------------------------------------------------------------------------------
class FakeAH:
    """An Avantis on MIDI over TCP: answers name and colour requests, records what Relay sends, can move a mute."""

    def __init__(self):
        self.got = bytearray()
        self.names = {(0, 0): b"Pastor", (0, 1): b"Keys"}
        self.writers = []

    async def handle(self, reader, writer):
        self.writers.append(writer)
        buf = bytearray()
        while True:
            data = await reader.read(4096)
            if not data:
                return
            self.got += data
            buf += data
            while True:
                s, e = buf.find(0xF0), buf.find(0xF7)
                if s < 0 or e < s:
                    break
                frame = bytes(buf[s:e + 1])
                del buf[:e + 1]
                h = len(ah.SYSEX)
                if frame[:h] == ah.SYSEX and frame[h + 1] == 0x01:
                    ch, n = frame[h], frame[h + 2]
                    if (ch, n) in self.names:
                        writer.write(ah.SYSEX + bytes([ch, 0x02, n]) + self.names[(ch, n)] + b"\xF7")
                elif frame[:h] == ah.SYSEX and frame[h + 1] == 0x03:
                    self.names[(frame[h], frame[h + 2])] = frame[h + 3:-1]

    def push(self, data: bytes):
        for w in self.writers:
            w.write(data)


class FakeYamaha:
    """A CL5 over RCP: keeps parameters, answers get, answers and notifies set, recalls scenes."""

    def __init__(self):
        self.params = {("MIXER:Current/InCh/Label/Name", 0, 0): '"Pastor"', ("MIXER:Current/InCh/Fader/On", 0, 0): "1",
                       ("MIXER:Current/InCh/Fader/Level", 0, 0): "-1000"}
        self.lines = []
        self.writers = []
        self.scene = 3

    async def handle(self, reader, writer):
        self.writers.append(writer)
        while True:
            line = await reader.readline()
            if not line:
                return
            text = line.decode().strip()
            self.lines.append(text)
            t = yamaha.tokens(text)
            if t[0] == "get" and len(t) >= 4:
                v = self.params.get((t[1], int(t[2]), int(t[3])), '""' if t[1].endswith("Name") else "0")
                writer.write(f"OK get {t[1]} {t[2]} {t[3]} {v}\n".encode())
            elif t[0] == "set" and len(t) >= 5:
                v = text.split(" ", 4)[4]
                self.params[(t[1], int(t[2]), int(t[3]))] = v
                writer.write(f"OK set {t[1]} {t[2]} {t[3]} {v}\n".encode())
            elif t[0] == "sscurrent_ex":
                writer.write(f"OK sscurrent_ex {t[1]} {self.scene}\n".encode())
            elif t[0] == "ssrecall_ex":
                self.scene = int(t[2])
                writer.write(f"OK ssrecall_ex {t[1]} {t[2]}\nNOTIFY sscurrent_ex {t[1]} {t[2]}\n".encode())
            elif t[0] == "ssinfo_ex":
                writer.write(f'OK ssinfo_ex {t[1]} {t[2]} "Scene {t[2]}" "" 0\n'.encode())

    def notify(self, line: str):
        for w in self.writers:
            w.write((line + "\n").encode())


class FakeSQ:
    """An SQ-5: answers NRPN get requests with what it holds."""

    def __init__(self):
        self.codec = ah.SQ(MODELS["sq5"], 1)
        self.values = {ah._param(ah.SQ_MUTE["in"], 0): (0, 1), ah._param(ah.SQ_LEVEL["in"], 0): ah.sq_level(-5.0)}
        self.got = bytearray()

    async def handle(self, reader, writer):
        p = ah.MidiParser()
        n = ah.NrpnState()
        while True:
            data = await reader.read(4096)
            if not data:
                return
            self.got += data
            msb = lsb = None
            for ev in p.feed(data):
                if ev[0] != "cc":
                    continue
                _, ch, ctl, val = ev
                if ctl == 0x63:
                    msb = val
                elif ctl == 0x62:
                    lsb = val
                elif ctl == 0x60 and val == 0x7F and msb is not None:
                    vc, vf = self.values.get((msb, lsb), (0, 0))
                    writer.write(bytes([0xB0, 0x63, msb, 0xB0, 0x62, lsb, 0xB0, 0x06, vc, 0xB0, 0x26, vf]))
                else:
                    got = n.cc(ch, ctl, val, fine=True)
                    if got:
                        self.values[(got[0], got[1])] = (got[2], got[3])


class FakeOsc(asyncio.DatagramProtocol):
    """An X32 or WING: answers a query (an address with no arguments) with what it holds and stores a set."""

    def __init__(self, wing: bool):
        self.wing = wing
        self.values = {}
        self.got = []
        self.transport = None
        self.peer = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.peer = addr
        for address, args in osc.parse(data):
            self.got.append((address, args))
            if address in ("/xremote", "/*S", "/xinfo"):
                if address == "/xinfo":
                    self.transport.sendto(osc.message("/xinfo", "10.0.0.5", "FOH", "X32", "4.06"), addr)
                continue
            if address == "/meters":
                blob = struct.pack("<i", 70) + struct.pack("<70f", *([0.5] + [0.0] * 69))
                self.transport.sendto(osc.message("/meters/0", blob), addr)
                continue
            if args:
                self.values[address] = args[0]
                if address == "/-action/goscene":
                    self.values["/-show/prepos/current"] = args[0]
                continue
            if address in self.values:
                v = self.values[address]
                if self.wing and isinstance(v, float):
                    self.transport.sendto(osc.message(address, f"{v:.1f}", 0.5, v), addr)
                else:
                    self.transport.sendto(osc.message(address, v), addr)

    def say(self, address, *args):
        self.transport.sendto(osc.message(address, *args), self.peer)


def free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def live_tests():
    fah, fya, fsq = FakeAH(), FakeYamaha(), FakeSQ()
    servers = [await asyncio.start_server(f.handle, "127.0.0.1", 0) for f in (fah, fya, fsq)]
    p_ah, p_ya, p_sq = (s.sockets[0].getsockname()[1] for s in servers)
    loop = asyncio.get_running_loop()
    fx = FakeOsc(False)
    fw = FakeOsc(True)
    tx, _ = await loop.create_datagram_endpoint(lambda: fx, local_addr=("127.0.0.1", 0))
    tw, _ = await loop.create_datagram_endpoint(lambda: fw, local_addr=("127.0.0.1", 0))
    p_x, p_w = tx.get_extra_info("sockname")[1], tw.get_extra_info("sockname")[1]
    fx.values.update({"/ch/01/config/name": "Lead", "/ch/01/mix/on": 1, "/ch/01/mix/fader": 0.75,
                      "/ch/01/mix/02/on": 1, "/ch/01/mix/02/level": 0.5, "/-show/prepos/current": 4,
                      "/-show/showfile/scene/004/name": "Sermon", "/ch/05/config/name": "Old"})
    fw.values.update({"/ch/1/name": "Choir", "/ch/1/mute": 1, "/ch/1/fdr": -10.0, "/$ctl/lib/$actidx": 2})

    config.save({"modules": {"audio_mixers": {"enabled": True}, "crew": {"enabled": False}}, "audio_mixers": svc.clean_settings({"desks": [
        {"id": "av", "name": "FOH", "model": "avantis", "host": "127.0.0.1", "port": p_ah},
        {"id": "cl", "name": "Monitors", "model": "cl5", "host": "127.0.0.1", "port": p_ya},
        {"id": "sq", "name": "Youth", "model": "sq5", "host": "127.0.0.1", "port": p_sq},
        {"id": "x", "name": "Chapel", "model": "x32", "host": "127.0.0.1", "port": p_x},
        {"id": "w", "name": "Hall", "model": "wing", "host": "127.0.0.1", "port": p_w}]})})
    _modules.attach("audio_mixers")
    s = svc.service
    await s.start()
    av, cl, sqd, x, w = (s.desk(i) for i in ("av", "cl", "sq", "x", "w"))
    try:
        with section("Allen & Heath follows the desk"):
            check("connects", await await_for(lambda: av.driver.connected()), av.driver.error)
            check("asks for names and reads them", await await_for(lambda: av.mirror.strips["in1"].name == "Pastor"))
            eq("an unknown mute stays unknown", av.mirror.strips["in1"].muted, None)
            fah.push(bytes([0x90, 0x01, 0x7F, 0x90, 0x01, 0x00]))
            check("a mute on the desk", await await_for(lambda: av.mirror.strips["in2"].muted is True))
            fah.push(bytes([0xB0, 0x63, 0x00, 0xB0, 0x62, 0x17, 0xB0, 0x06, 0x57]))
            check("a fader on the desk", await await_for(lambda: av.mirror.strips["in1"].db == -10.0))
            fah.push(bytes([0xB0, 0x00, 0x00, 0xC0, 0x04]))
            check("a scene on the desk", await await_for(lambda: av.mirror.scene == 5))
            eq("Relay mutes", s.control("av", "mute", "in3", True), "")
            check("the desk got the note", await await_for(lambda: bytes([0x90, 0x02, 0x7F, 0x90, 0x02, 0x00]) in fah.got))
            eq("the mirror shows it (A&H does not echo)", av.mirror.strips["in3"].muted, True)
            eq("a scene out of range", s.control("av", "scene", None, 9999), "No such scene on this desk.")

        with section("Yamaha follows the desk"):
            check("connects and reads", await await_for(lambda: cl.mirror.strips["in1"].name == "Pastor"), cl.driver.error)
            eq("its level", cl.mirror.strips["in1"].db, -10.0)
            eq("on is unmuted", cl.mirror.strips["in1"].muted, False)
            check("its scene and title", await await_for(lambda: cl.mirror.scene == 3 and cl.mirror.scene_name() == "Scene 3"))
            fya.notify('NOTIFY set MIXER:Current/InCh/Fader/On 1 0 0 "OFF"')
            check("a mute on the desk", await await_for(lambda: cl.mirror.strips["in2"].muted is True))
            eq("Relay sets a level", s.control("cl", "level", "in2", -5), "")
            check("the desk answers it", await await_for(lambda: cl.mirror.strips["in2"].db == -5.0))
            eq("Relay recalls", s.control("cl", "scene", None, 7), "")
            check("the scene follows", await await_for(lambda: cl.mirror.scene == 7))

        with section("SQ answers get requests"):
            check("connects", await await_for(lambda: sqd.driver.connected()), sqd.driver.error)
            check("input 1 is muted, at -5 dB", await await_for(lambda: sqd.mirror.strips["in1"].muted is True
                                                                  and sqd.mirror.strips["in1"].db == -5.0))
            eq("no names on an SQ", s.control("sq", "name", "in1", "Vox"), drv.NO_NAMES)
            eq("a mute goes out", s.control("sq", "mute", "mix1", True), "")
            check("as an NRPN", await await_for(lambda: sqd.driver.codec.mute("mix", 1, True) in fsq.got))

        with section("X32 follows the desk"):
            check("answers", await await_for(lambda: x.mirror.strips["in1"].name == "Lead"), x.driver.error)
            check("keeps the subscription", ("/xremote", []) in fx.got)
            eq("its fader", x.mirror.strips["in1"].db, 0.0)
            eq("on is unmuted", x.mirror.strips["in1"].muted, False)
            check("a send", await await_for(lambda: x.mirror.strips["in1"].sends.get(2) == (True, -10.0)))
            check("the scene, counted from 1", await await_for(lambda: x.mirror.scene == 5 and x.mirror.scene_name() == "Sermon"))
            check("meters", await await_for(lambda: x.mirror.strips["in1"].meter is not None))
            eq("input 1 at -6 dBFS", round(x.mirror.strips["in1"].meter), -6)
            fx.say("/ch/02/mix/on", 0)
            check("a mute on the desk", await await_for(lambda: x.mirror.strips["in2"].muted is True))
            eq("Relay renames", s.control("x", "name", "in3", "Bass Guitar DI 2"), "")
            check("cut to twelve characters", await await_for(lambda: fx.values.get("/ch/03/config/name") == "Bass Guitar"))
            check("and read back", await await_for(lambda: x.mirror.strips["in3"].name == "Bass Guitar"))

        with section("WING follows the desk"):
            check("answers", await await_for(lambda: w.mirror.strips["in1"].name == "Choir"), w.driver.error)
            check("subscribes", ("/*S", []) in fw.got)
            eq("its fader from ,sff", w.mirror.strips["in1"].db, -10.0)
            eq("mute 1 is muted", w.mirror.strips["in1"].muted, True)
            eq("Relay recalls", s.control("w", "scene", None, 3), "")
            check("by index, then GO", await await_for(lambda: ("/$ctl/lib/$action", ["GO"]) in fw.got
                                                       and fw.values.get("/$ctl/lib/$actionidx") == 3))

        with section("desks are gear and channels"):
            await asyncio.to_thread(s.report)
            mine = await asyncio.to_thread(channels.of_provider, "audio_mixers")
            names = {e["key"]: e["gear_name"] for c in mine for e in c["ends"] if e["provider"] == "audio_mixers"}
            eq("an input", names.get("x:in1"), "Lead")
            check("a mix is a destination", any(e["key"] == "x:mix2" and e["direction"] == "destination"
                                                for c in mine for e in c["ends"]))
            eq("an unnamed input shows its number", names.get("x:in4"), "Input 4")
            check("five desks are gear", len({e["gear"] for c in mine for e in c["ends"]
                                               if e["provider"] == "audio_mixers"}) == 5)

        with section("a rename on the Network page reaches the desk"):
            ref = next(c["ref"] for c in mine if any(e["key"] == "x:in1" for e in c["ends"]))
            cid = channels.channel_id(ref)
            await asyncio.to_thread(channels.rename_on_device, cid, "Pastor Mic")
            check("the X32 got it", await await_for(lambda: fx.values.get("/ch/01/config/name") == "Pastor Mic"))
            sq_ref = next(c["ref"] for c in mine if any(e["key"] == "sq:in1" for e in c["ends"]))
            refused = None
            try:
                await asyncio.to_thread(channels.rename_on_device, channels.channel_id(sq_ref), "Vox")
            except channels.Refused as e:
                refused = e
            check("an SQ cannot take it", refused is not None)

        with section("sends are routes"):
            dest = next(c["ref"] for c in mine if any(e["key"] == "cl:mix2" for e in c["ends"]))
            src = next(c["ref"] for c in mine if any(e["key"] == "cl:in1" for e in c["ends"]))
            got = await asyncio.to_thread(routing.set, dest, src, -6.0)
            eq("no problem", got["problems"], [])
            check("the send is on", await await_for(lambda: fya.params.get((yamaha.SEND_ON, 0, 1)) == "1"))
            eq("at -6 dB", fya.params.get((yamaha.SEND_LEVEL, 0, 1)), "-600")
            check("an input already off is left alone", (yamaha.SEND_ON, 1, 1) not in fya.params)
            st = await asyncio.to_thread(routing.status, dest)
            check("the gear says the same", await await_for(lambda: routing.status(dest)["state"] == "same"), st)
            av_dest = next(c["ref"] for c in mine if any(e["key"] == "av:mix1" for e in c["ends"]))
            av_src = next(c["ref"] for c in mine if any(e["key"] == "av:in1" for e in c["ends"]))
            await asyncio.to_thread(routing.set, av_dest, av_src, 0.0)
            check("Avantis gets SysEx 0D", await await_for(lambda: av.driver.codec.send(1, 1, 0.0) in fah.got))
            eq("Avantis cannot say", (await asyncio.to_thread(routing.status, av_dest))["state"], "wanted")

        with section("watchdog"):
            sent = []
            registry.provide("crew.notify", lambda message, ids: sent.append((message, list(ids))) or 1, owner="crew")
            svc.save({"watchdog": {"on": True, "user_id": 7, "names": True, "scene": True, "mutes": ["x:main1"]}})
            fx.values["/main/st/mix/on"] = 1
            x.driver.osc("/main/st/mix/on")
            await await_for(lambda: x.mirror.strips["main1"].muted is False)
            eq("baseline saved", (await asyncio.to_thread(s.save_baseline, "x"))["scene"], 5)
            eq("nothing differs", s.differences(x), [])
            fx.say("/ch/05/config/name", "Changed")
            fx.say("/main/st/mix/on", 0)
            await await_for(lambda: x.mirror.strips["main1"].muted is True)
            eq("two differences", len(s.differences(x)), 2)
            out = await s.watch()
            check("one notification to the chosen person", len(sent) == 1 and sent[0][1] == [7], sent)
            check("names what changed", "Changed" in sent[0][0] and "muted" in sent[0][0], sent)
            await s.watch()
            eq("not sent twice", len(sent), 1)
            s.control("x", "mute", "main1", False)
            await await_for(lambda: x.mirror.strips["main1"].muted is False)
            eq("Relay's own change is expected", [d["id"] for d in s.differences(x)], ["name:in5:Changed"])
            check("the pill says so", MODULE.pills() and "1" in MODULE.pills()[0]["text"])
            eq("a recall from Relay is expected", s.control("x", "scene", None, 9), "")
            check("and the baseline follows the scene", svc.settings()["baselines"]["x"]["scene"] == 9)
            registry.withdraw("crew.notify")

        with section("mic names from Planning Center"):
            eq("off without Planning Center", s.name_preview()["problem"],
               "Turn on Planning Center to name channels from the plan.")
            in2 = next(c["ref"] for c in mine if any(e["key"] == "x:in2" for e in c["ends"]))
            registry.provide("plan.rundown_now", lambda: {"plan": {"title": "Sunday"}, "mics": [
                {"mic": "2", "position": "Vocals", "channel_ref": in2, "person": {"name": "Anna Smith"}},
                {"mic": "9", "position": "Keys", "channel_ref": "", "person": {"name": "Ben"}}]},
                owner="planning_center")
            got = await asyncio.to_thread(s.name_preview)
            eq("one row on a desk", [(r["key"], r["proposed"]) for r in got["rows"]], [("x:in2", "Anna")])
            eq("the plan", got["plan"], "Sunday")
            check("nothing sent by the preview", fx.values.get("/ch/02/config/name") is None)
            out = s.push_names([{"key": "x:in2", "name": "Anna"}])
            eq("sent", out, {"sent": 1, "problems": []})
            check("the desk has it", await await_for(lambda: fx.values.get("/ch/02/config/name") == "Anna"))
            registry.withdraw("plan.rundown_now")

        with section("boards"):
            items = [{"id": "f", "type": "audio_mixers.faders", "props": {"desk": "x", "strips": "main1, in1-2"}},
                     {"id": "m", "type": "audio_mixers.mute", "props": {"desk": "x", "strip": "in2"}},
                     {"id": "s", "type": "audio_mixers.scene", "props": {"desk": "x", "scene": "9"}},
                     {"id": "wl", "type": "audio_mixers.wall", "props": {"desk": "x", "strips": "in1-2"}},
                     {"id": "mt", "type": "audio_mixers.meters", "props": {"desk": "x", "strips": "in1"}},
                     {"id": "mc", "type": "audio_mixers.mics", "props": {}}]
            await await_for(lambda: x.mirror.scene == 9)
            data = await MODULE.board_data(items)
            eq("three strips", [x_["id"] for x_ in data["f"]["strips"]], ["main1", "in1", "in2"])
            eq("its position", data["f"]["strips"][1]["pos"], 0.75)
            eq("a muted button is lit", data["m"], {"lit": True})
            eq("the current scene is lit", data["s"], {"lit": True})
            check("the wall lists channels", "Pastor Mic" in data["wl"]["rows"][0]["text"], data["wl"])
            check("meters", "▮" in data["mt"]["rows"][0]["text"], data["mt"])
            check("mic states", data["mc"]["rows"] and any("Pastor Mic" in line for line in s.mic_lines()), data["mc"])
            from starlette.requests import Request

            def request(body: bytes) -> Request:
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                return Request({"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")],
                                "path": "/", "query_string": b""}, receive)
            got = await MODULE.board_press(items[0], request(b'{"strip": "in1", "pos": 0.5}'))
            eq("a fader move", got, {"ok": True})
            check("reaches the desk", await await_for(lambda: fx.values.get("/ch/01/mix/fader") == 0.5))
            got = await MODULE.board_press(items[0], request(b'{"strip": "in9", "pos": 0.5}'))
            eq("not on this bank", got["ok"], False)
            got = await MODULE.board_press(items[1], request(b""))
            eq("the mute button unmutes", got, {"ok": True})
            check("the desk unmutes", await await_for(lambda: fx.values.get("/ch/02/mix/on") == 1))
            data = await MODULE.board_data(items[:1])
            eq("a bank on a desk that is gone shows nothing", (await MODULE.board_data(
                [{"id": "z", "type": "audio_mixers.faders", "props": {"desk": "nope", "strips": "in1"}}]))["z"]["strips"][0]["id"],
               "in1")

        with section("a desk that goes away"):
            for srv in servers[:1]:
                srv.close()
            for wtr in fah.writers:
                wtr.close()
            check("it says so", await await_for(lambda: not av.driver.connected()))
            eq("control refuses", s.control("av", "mute", "in1", True), drv.NOT_CONNECTED)
            lvl, detail = s.health()
            check("health names it", lvl == "warn" and "FOH" in detail, (lvl, detail))
    finally:
        await s.stop()
        for srv in servers:
            srv.close()
        tx.close()
        tw.close()


asyncio.run(live_tests())

# -- admin routes ---------------------------------------------------------------------------------------------------
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"audio_mixers": {"enabled": True}},
             "audio_mixers": svc.clean_settings({})})
_modules.attach("audio_mixers")
svc.service.running = True        # the registry starts it with the app; here the routes' own loop runs the drivers
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", username="op1", base_url="http://relay.local:8001")
A = "/api/admin/audio_mixers"

with section("settings and permissions"):
    r = admin.put(A + "/settings", json={"desks": [{"model": "x32", "name": "FOH", "host": "127.0.0.1",
                                                    "port": free_port(socket.SOCK_DGRAM)}]})
    eq("saved", r.status_code, 200)
    did = r.json()["settings"]["desks"][0]["id"]
    check("a new id", did.startswith("d"), did)
    eq("a bad address", admin.put(A + "/settings", json={"desks": [{"model": "x32", "host": "not a host!"}]}).status_code, 400)
    eq("an unknown model", admin.put(A + "/settings", json={"desks": [{"model": "nope"}]}).status_code, 400)
    eq("nine desks", admin.put(A + "/settings", json={"desks": [{"model": "x32"}] * 9}).status_code, 400)
    eq("a viewer cannot set up", viewer.put(A + "/settings", json={"desks": []}).status_code, 403)
    eq("a viewer can read", viewer.get(A).status_code, 200)
    check("a viewer does not see the watchdog", "watchdog" not in viewer.get(A).json()["settings"])
    eq("a viewer cannot mute", viewer.post(A + f"/{did}/control", json={"action": "mute", "strip": "in1",
                                                                          "value": True}).status_code, 403)
    eq("an operator may, but the desk is not connected", operator.post(A + f"/{did}/control", json={
        "action": "mute", "strip": "in1", "value": True}).json(), {"error": drv.NOT_CONNECTED})
    eq("nothing to do", operator.post(A + f"/{did}/control", json={"action": "explode"}).status_code, 400)
    eq("an operator cannot save a baseline", operator.post(A + f"/{did}/baseline").status_code, 403)
    eq("not connected: no baseline", admin.post(A + f"/{did}/baseline").status_code, 409)
    eq("names need a confirm", operator.post(A + "/names", json={"rows": [{"key": "x", "name": "y"}]}).status_code, 400)
    eq("a viewer cannot preview names", viewer.get(A + "/names").status_code, 403)
    eq("people for the watchdog", admin.get(A + "/people").status_code, 200)
    eq("the long poll answers", admin.get(A + "/wait?since=-5").status_code, 200)
    check("models listed, all untested", all(not m["tested"] for m in admin.get(A).json()["models"]))

with section("a connected desk through the routes"):
    d = svc.service.desk(did) or None
    check("the desk exists", d is not None)
    mirror = Mirror(MODELS["x32"])
    d.mirror = mirror
    d.driver = drv.make(d.cfg, mirror)

    class Wire:
        """Stands in for the OS's UDP socket so the route can be checked without a desk (the live tests above use one)."""

        def __init__(self):
            self.sent = []

        def sendto(self, data, addr=None):
            self.sent.extend(osc.parse(data))

        def close(self):
            pass

    wire = Wire()
    d.driver.transport, d.driver.peer = wire, ("127.0.0.1", 10023)
    mirror.link(True)
    mirror.heard()
    r = operator.post(A + f"/{did}/control", json={"action": "mute", "strip": "in1", "value": True})
    eq("muted", r.json(), {"ok": True})
    eq("sent", wire.sent[0], ("/ch/01/mix/on", [0]))
    eq("a level", operator.post(A + f"/{did}/control", json={"action": "level", "strip": "in1", "value": -10}).json(),
       {"ok": True})
    eq("as the X32's number", wire.sent[2], ("/ch/01/mix/fader", [0.5]))
    eq("no such strip", operator.post(A + f"/{did}/control", json={"action": "mute", "strip": "in99",
                                                                    "value": True}).status_code, 409)

done()
