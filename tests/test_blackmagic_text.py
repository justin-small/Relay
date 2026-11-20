"""Blackmagic module, the text-protocol devices (#432 HyperDeck, #433 Videohub) and the rename from "atem".

No real HyperDeck or Videohub was used: the fakes answer the way Blackmagic's protocol documents say (HyperDeck
Ethernet Protocol 1.x: `500 connection info:`, `208`/`508 transport info:`, `202`/`502 slot info:`, `205 clips info:`;
Videohub Ethernet Protocol 2.x: the prelude of blocks, then `ACK`/`NAK`). Parsing is tested on transcripts, the
connection against fake servers on loopback, the admin routes against clients whose sends are recorded.
Run: python tests/test_blackmagic_text.py
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
from app import config, routing as core_routing  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.blackmagic import hyperdeck as hd  # noqa: E402
from app.modules.blackmagic import service as svc  # noqa: E402
from app.modules.blackmagic import textclient, videohub as vh  # noqa: E402

from _harness import await_for, check, done, eq, section  # noqa: E402

CRLF = "\r\n"


def deck_msg(code, head, **fields):
    """A HyperDeck message the way the protocol writes it."""
    body = "".join(f"{k.replace('_', ' ')}: {v}{CRLF}" for k, v in fields.items())
    return f"{code} {head}:{CRLF}{body}{CRLF}".encode()


GREETING = deck_msg(500, "connection info", protocol_version="1.11", model="HyperDeck Studio Mini")
TRANSPORT = deck_msg(208, "transport info", status="stopped", speed=0, slot_id=1, display_timecode="00:00:10:00",
                     timecode="00:00:10:00", clip_id=2, video_format="1080p25", loop="false")
SLOT1 = deck_msg(202, "slot info", slot_id=1, status="mounted", volume_name="SSD", recording_time=3600,
                 video_format="1080p25")
SLOT2 = deck_msg(202, "slot info", slot_id=2, status="empty", volume_name="", recording_time=0)
CLIPS = (b"205 clips info:\r\nclip count: 2\r\n1: Take one.mov 00:00:00:00 00:00:10:00\r\n"
         b"2: Take two.mov 00:00:10:00 00:00:20:00\r\n\r\n")


class Sent:
    """A client whose sends are recorded instead of written."""

    def __init__(self, client):
        self.sent = []
        client.send_text = lambda text: (self.sent.append(text), True)[1]


# ------------------------------------------------------------------------------------------------ HyperDeck: reading
with section("HyperDeck: the greeting and what it reports"):
    c = hd.HyperDeckClient("127.0.0.1")
    rec = Sent(c)
    changes = []
    c.on_change = lambda: changes.append(1)
    for chunk in (GREETING[:20], GREETING[20:]):                      # a message cut anywhere still reads
        c._buf = c.consume(c._buf + chunk)
    check("connected once the deck introduces itself", c.connected)
    eq("model and protocol", (c.state.model, c.state.protocol), ("HyperDeck Studio Mini", "1.11"))
    check("it asks to be told about changes and reads the state",
          any(t.startswith("notify: remote: true transport: true slot: true") for t in rec.sent)
          and "clips get" in rec.sent[-1])
    c.consume(TRANSPORT + SLOT1 + SLOT2 + CLIPS)
    st = c.state
    eq("transport", (st.status, st.slot, st.clip_id, st.timecode, st.video_format, st.loop),
       ("stopped", 1, 2, "00:00:10:00", "1080p25", False))
    eq("slots", [(s["id"], s["status"], s["volume"], s["seconds_left"]) for s in st.slots.values()],
       [(1, "mounted", "SSD", 3600), (2, "empty", "", 0)])
    eq("clips, with names that hold spaces", [(x["id"], x["name"], x["duration"]) for x in st.clips],
       [(1, "Take one.mov", "00:00:10:00"), (2, "Take two.mov", "00:00:20:00")])
    eq("the current clip", c.current_clip()["name"], "Take two.mov")
    c.consume(deck_msg(508, "transport info", status="record", slot_id=1, clip_id=3, display_timecode="00:00:11:05"))
    eq("a notification updates it", (st.status, st.clip_id, st.timecode), ("record", 3, "00:00:11:05"))
    rec.sent.clear()
    c.consume(deck_msg(508, "transport info", status="stopped", slot_id=1, clip_id=3))
    eq("a recording that ends has the clips read again", rec.sent, ["clips get\r\n"])
    c.consume(deck_msg(508, "transport info", status="bogus", slot_id=1, clip_id=3))
    eq("a status the protocol does not have is ignored", st.status, "stopped")
    c.consume(deck_msg(512, "remote info", enabled="false", override="false"))
    eq("remote control off is noticed", st.remote_enabled, False)
    c.consume(b"105 no disk\r\n")
    eq("an error is a fixed sentence, never the deck's own text", st.notice, hd.REFUSED[105])
    c.consume(b"200 ok\r\n")
    eq("and the next ok clears it", st.notice, "")
    c.consume(b"999 who knows\r\nnot a message\r\n\r\n")
    check("junk is ignored", c.connected)
    c.consume(deck_msg(202, "slot info", slot_id=9, status="mounted"))
    eq("a slot that cannot exist is ignored", sorted(st.slots), [1, 2])
    big = b"205 clips info:\r\n" + b"".join(f"{i}: c{i}.mov 00:00:00:00 00:00:01:00\r\n".encode() for i in range(1, 400)) + b"\r\n"
    c.consume(big)
    eq("at most MAX_CLIPS are kept", len(st.clips), hd.MAX_CLIPS)

with section("HyperDeck: commands and polling"):
    c = hd.HyperDeckClient("127.0.0.1")
    rec = Sent(c)
    c.connected = True
    c.state.slot, c.state.status = 2, "record"
    rec.sent.clear()
    c.record(), c.stop(), c.play()
    eq("the three commands", rec.sent, ["record\r\n", "stop\r\n", "play\r\n"])
    rec.sent.clear()
    c._slot_asked = 0.0
    c.tick(100.0)
    eq("while recording, the slot is asked for its time left", rec.sent, ["slot info: slot id: 2\r\n"])
    rec.sent.clear()
    c.tick(101.0)
    eq("and not again straight away", rec.sent, [])
    c.ping()
    eq("a ping is a harmless command", rec.sent, ["ping\r\n"])
    real = hd.HyperDeckClient("127.0.0.1")
    eq("a client with no connection sends nothing", real.send_text("record\r\n"), False)


# ------------------------------------------------------------------------------------------------ HyperDeck: wire
class FakeDeck:
    """A HyperDeck on loopback. `refuse` is an error code the next record gets."""

    def __init__(self):
        self.status, self.clips, self.refuse, self.seen, self.quiet = "stopped", [("Take one.mov", 10)], 0, [], False
        self.writers, self.server = [], None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        self.writers.append(writer)
        writer.write(GREETING)
        try:
            while not reader.at_eof():
                line = (await reader.readline()).decode().strip()
                if not line:
                    continue
                self.seen.append(line)
                if self.quiet:
                    continue
                writer.write(self.answer(line))
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass

    def transport(self, code=208):
        return deck_msg(code, "transport info", status=self.status, speed=0, slot_id=1, clip_id=len(self.clips),
                        display_timecode="00:00:01:00", video_format="1080p25", loop="false")

    def answer(self, line):
        if line == "transport info":
            return self.transport()
        if line.startswith("slot info"):
            sid = line.rsplit(":", 1)[1].strip()
            return SLOT1 if sid == "1" else SLOT2
        if line == "clips get":
            rows = "".join(f"{i}: {n} 00:00:00:00 00:00:{d:02d}:00\r\n" for i, (n, d) in enumerate(self.clips, 1))
            return f"205 clips info:\r\nclip count: {len(self.clips)}\r\n{rows}\r\n".encode()
        if line == "record":
            if self.refuse:
                code, self.refuse = self.refuse, 0
                return f"{code} refused\r\n".encode()
            self.status = "record"
            return b"200 ok\r\n" + self.transport(508)
        if line == "stop":
            was, self.status = self.status, "stopped"
            if was == "record":
                self.clips.append((f"Take {len(self.clips) + 1}.mov", 5))
            return b"200 ok\r\n" + self.transport(508)
        if line.startswith(("notify", "play", "ping")):
            return b"200 ok\r\n"
        return b"100 syntax error\r\n"

    async def stop(self):
        self.server.close()
        for w in self.writers:
            w.close()


async def deck_wire():
    out = {}
    deck = FakeDeck()
    port = await deck.start()
    c = hd.HyperDeckClient("127.0.0.1", port)
    c.start()
    out["connected"] = await await_for(lambda: c.connected and c.state.clips and 1 in c.state.slots)
    out["state"] = (c.state.model, c.state.status, [x["name"] for x in c.state.clips], c.state.slots[1]["seconds_left"])
    c.record()
    out["recording"] = await await_for(lambda: c.state.status == "record")
    c.stop()
    out["stopped"] = await await_for(lambda: c.state.status == "stopped")
    out["new clip"] = await await_for(lambda: len(c.state.clips) == 2)
    deck.refuse = 105
    c.record()
    out["refused"] = await await_for(lambda: c.state.notice == hd.REFUSED[105])
    out["still stopped"] = c.state.status
    # The deck closes the connection: the client says so and comes back by itself.
    for w in list(deck.writers):
        w.close()
    out["dropped"] = await await_for(lambda: not c.connected, 3)
    out["error"] = c.error
    out["back"] = await await_for(lambda: c.connected, 6)
    # A deck that stops answering is dropped after QUIET_S.
    textclient.QUIET_S = 1.2
    deck.quiet = True
    for w in list(deck.writers):
        w.write(b"")
    out["quiet"] = await await_for(lambda: not c.connected, 8)
    out["quiet error"] = c.error
    deck.quiet = False
    textclient.QUIET_S = 30.0
    await c.close()
    out["closed"] = c._task is None and not c.connected
    await deck.stop()
    return out


with section("HyperDeck: against a fake deck on loopback"):
    got = asyncio.run(deck_wire())
    check("it connects and reads everything", got["connected"])
    eq("the state", got["state"], ("HyperDeck Studio Mini", "stopped", ["Take one.mov"], 3600))
    check("record starts a recording", got["recording"])
    check("stop ends it", got["stopped"])
    check("and the new clip is read", got["new clip"])
    check("a refused record says why", got["refused"])
    eq("and the deck stays stopped", got["still stopped"], "stopped")
    check("a closed connection is noticed", got["dropped"])
    eq("with a sentence", got["error"], "The HyperDeck closed the connection")
    check("and it reconnects by itself", got["back"])
    check("a deck that goes quiet is dropped", got["quiet"])
    eq("with a reason", got["quiet error"], "The HyperDeck stopped answering")
    check("close stops the task", got["closed"])


# ------------------------------------------------------------------------------------------------ Videohub: reading
def hub_block(head, *rows):
    return (head + "\n" + "".join(r + "\n" for r in rows) + "\n").encode()


def prelude(routing=None, locks=None):
    routing = routing or {0: 0, 1: 1, 2: 2}
    return (hub_block("PROTOCOL PREAMBLE:", "Version: 2.8")
            + hub_block("VIDEOHUB DEVICE:", "Device present: true", "Model name: Blackmagic Smart Videohub 20 x 20",
                        "Unique ID: AABB11", "Video inputs: 3", "Video processing units: 0", "Video outputs: 3",
                        "Video monitoring outputs: 0", "Serial ports: 0")
            + hub_block("INPUT LABELS:", "0 Camera 1", "1 Camera 2", "2 Slides")
            + hub_block("OUTPUT LABELS:", "0 Main", "1 Overflow", "2 Stream")
            + hub_block("VIDEO OUTPUT LOCKS:", *[f"{o} {(locks or {}).get(o, 'U')}" for o in range(3)])
            + hub_block("VIDEO OUTPUT ROUTING:", *[f"{o} {i}" for o, i in sorted(routing.items())])
            + hub_block("END PRELUDE:"))


with section("Videohub: the prelude and later changes"):
    c = vh.VideohubClient("127.0.0.1")
    rec = Sent(c)
    data = prelude()
    c._buf = c.consume(data[:40])
    check("half a block is held back", not c.connected and c._buf)
    c._buf = c.consume(c._buf + data[40:])
    st = c.state
    check("ready at END PRELUDE", c.connected and st.ready)
    eq("device", (st.version, st.model, st.uid, st.inputs, st.outputs, st.present),
       ("2.8", "Blackmagic Smart Videohub 20 x 20", "aabb11", 3, 3, True))
    eq("labels", (st.input_labels, st.output_labels),
       ({0: "Camera 1", 1: "Camera 2", 2: "Slides"}, {0: "Main", 1: "Overflow", 2: "Stream"}))
    eq("routing", st.routing, {0: 0, 1: 1, 2: 2})
    eq("a change arrives as a block", (c.consume(hub_block("VIDEO OUTPUT ROUTING:", "1 2")), st.routing[1]), (b"", 2))
    c.consume(hub_block("VIDEO OUTPUT LOCKS:", "0 L", "2 O", "1 Q"))
    eq("locks, and a mode the protocol lacks is ignored", st.locks, {0: "L", 1: "U", 2: "O"})
    c.consume(hub_block("OUTPUT LABELS:", "1 Overflow room"))
    eq("a renamed output", st.output_labels[1], "Overflow room")
    c.consume(hub_block("VIDEO OUTPUT ROUTING:", "7 99999", "x 1", "99999 1"))
    eq("junk rows are ignored", st.routing, {0: 0, 1: 2, 2: 2})
    c.consume(b"\r\n")
    c._block = None
    c.consume(b"ACK\r\n\r\n")
    eq("ack with nothing asked is harmless", st.notice, "")

with section("Videohub: commands"):
    c = vh.VideohubClient("127.0.0.1")
    rec = Sent(c)
    c._send = lambda text, _s=c._send: (rec.sent.append(text), c._asked.append(text.split(":", 1)[0]), True)[2]
    c.consume(prelude(locks={0: "L", 2: "O"}))
    eq("an output another surface holds cannot be routed", (c.can_route(0), c.can_route(1), c.can_route(2)),
       (False, True, True))
    c.route({2: 0, 1: 2})
    eq("routes go in one block, from zero, in order", rec.sent[-1], "VIDEO OUTPUT ROUTING:\n1 2\n2 0\n\n")
    eq("nothing to send is not sent", (c.route({}), len(rec.sent)), (False, 1))
    c.set_labels("output", {1: "  Over   flow\x07 ", 0: "Main"})
    eq("labels are one clean line", rec.sent[-1], "OUTPUT LABELS:\n0 Main\n1 Over flow\n\n")
    c.set_labels("input", {2: "Slides"})
    eq("input labels", rec.sent[-1], "INPUT LABELS:\n2 Slides\n\n")
    c.lock(1, True)
    c.lock(1, False)
    eq("lock and unlock", rec.sent[-2:], ["VIDEO OUTPUT LOCKS:\n1 O\n\n", "VIDEO OUTPUT LOCKS:\n1 U\n\n"])
    c.consume(b"NAK\n\n")
    eq("a refusal is a fixed sentence", c.state.notice, vh.REFUSED)
    c.consume(b"ACK\n\n")
    eq("the next ack clears it", c.state.notice, "")
    eq("clean_label", (vh.clean_label("  a\tb  "), vh.clean_label(5), vh.clean_label("x" * 90) == "x" * 60),
       ("a b", "", True))
    eq("a client with no connection sends nothing", vh.VideohubClient("127.0.0.1").route({0: 1}), False)


# ------------------------------------------------------------------------------------------------ Videohub: wire
class FakeHub:
    def __init__(self):
        self.routing, self.locks, self.seen, self.writers = {0: 0, 1: 1, 2: 2}, {0: "U", 1: "U", 2: "U"}, [], []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        self.writers.append(writer)
        writer.write(prelude(self.routing, self.locks))
        await writer.drain()
        block = []
        try:
            while not reader.at_eof():
                line = (await reader.readline()).decode().strip()
                if line:
                    block.append(line)
                    continue
                if block:
                    self.seen.append(list(block))
                    writer.write(self.answer(block))
                    await writer.drain()
                    block = []
        except (ConnectionError, asyncio.CancelledError):
            pass

    def answer(self, block):
        head, rows = block[0], block[1:]
        if head == "VIDEO OUTPUT ROUTING:":
            pairs = [tuple(int(x) for x in r.split()) for r in rows]
            if any(self.locks[o] == "L" for o, _ in pairs):
                return b"NAK\n\n"
            self.routing.update(pairs)
            return b"ACK\n\n" + hub_block(head, *[f"{o} {i}" for o, i in pairs])
        if head == "VIDEO OUTPUT LOCKS:":
            for r in rows:
                o, mode = r.split()
                self.locks[int(o)] = mode
            return b"ACK\n\n" + hub_block(head, *rows)
        if head in ("INPUT LABELS:", "OUTPUT LABELS:"):
            return b"ACK\n\n" + hub_block(head, *rows)
        if head == "PING:":
            return b"ACK\n\n"
        return b"NAK\n\n"

    async def stop(self):
        self.server.close()
        for w in self.writers:
            w.close()


async def hub_wire():
    out = {}
    hub = FakeHub()
    hub.locks[2] = "L"
    port = await hub.start()
    c = vh.VideohubClient("127.0.0.1", port)
    c.start()
    out["connected"] = await await_for(lambda: c.connected)
    out["model"] = c.state.model
    c.route({0: 2})
    out["routed"] = await await_for(lambda: c.state.routing.get(0) == 2)
    out["hub saw"] = hub.seen[-1]
    c.route({2: 0})
    out["nak"] = await await_for(lambda: c.state.notice == vh.REFUSED)
    out["routing kept"] = c.state.routing[2]
    c.lock(1, True)
    out["locked by us"] = await await_for(lambda: c.state.locks.get(1) == "O")
    c.ping()
    await asyncio.sleep(0.1)
    out["ping answered"] = hub.seen[-1] == ["PING:"]
    for w in list(hub.writers):
        w.close()
    out["dropped"] = await await_for(lambda: not c.connected, 3)
    out["back"] = await await_for(lambda: c.connected, 6)
    await c.close()
    await hub.stop()
    return out


with section("Videohub: against a fake router on loopback"):
    got = asyncio.run(hub_wire())
    check("it connects after the prelude", got["connected"])
    eq("the model", got["model"], "Blackmagic Smart Videohub 20 x 20")
    check("a route is sent and the router's update read", got["routed"])
    eq("as the protocol writes it", got["hub saw"], ["VIDEO OUTPUT ROUTING:", "0 2"])
    check("a refusal is noticed", got["nak"])
    eq("and the route is unchanged", got["routing kept"], 2)
    check("a lock Relay takes is shown as its own", got["locked by us"])
    check("a ping is sent", got["ping answered"])
    check("a closed connection is noticed", got["dropped"])
    check("and it reconnects", got["back"])


# ------------------------------------------------------------------------------------------------ settings
with section("Settings: kinds, ports, presets, and devices from before kinds"):
    old = svc.clean_device({"id": "a1", "host": "10.0.0.5", "port": 9910, "tally": False})
    eq("a device with no kind is a switcher", (old["kind"], old["name"], old["tally"]), ("atem", "Switcher", False))
    deck = svc.clean_device({"id": "d1", "kind": "hyperdeck", "host": "10.0.0.7", "port": 0, "tally": True})
    eq("a HyperDeck: its own port and name, no tally", (deck["port"], deck["name"], "tally" in deck), (9993, "HyperDeck", False))
    hub = svc.clean_device({"id": "h1", "kind": "videohub", "host": "10.0.0.8", "port": "x"})
    eq("a Videohub: its port, no presets yet", (hub["port"], hub["name"], hub["presets"], "tally" in hub), (9990, "Videohub", [], False))
    eq("an unknown kind is a switcher", svc.clean_device({"id": "z", "kind": "mixer"})["kind"], "atem")
    junk = [{"id": "p1", "name": " Program   to overflow ", "routes": {"0": 3, "1": 2, "x": 1, "9999": 1, "2": -1, "3": True}},
            {"id": "p1", "name": "Again", "routes": {"0": 1}}, {"id": "BAD ID", "name": "n", "routes": {"0": 1}},
            {"id": "p2", "name": "", "routes": {"0": 1}}, {"id": "p3", "name": "No routes", "routes": {}}, "junk",
            {"id": "p4", "name": "Fine", "routes": {"0": 0}}]
    got = svc.clean_presets(junk)
    eq("presets keep clean routes, drop repeats and nameless or empty ones", got,
       [{"id": "p1", "name": "Program to overflow", "routes": {"0": 3, "1": 2}}, {"id": "p4", "name": "Fine", "routes": {"0": 0}}])
    eq("at most MAX_PRESETS", len(svc.clean_presets([{"id": f"p{i}", "name": "n", "routes": {"0": 0}} for i in range(40)])),
       svc.MAX_PRESETS)
    many = svc.clean_settings({"devices": [{"id": "a1", "host": "10.0.0.5"}, {"id": "d1", "kind": "hyperdeck", "host": "10.0.0.7"}]})
    eq("a mixed list keeps every kind", [d["kind"] for d in many["devices"]], ["atem", "hyperdeck"])
    eq("tally overlaps count only switchers",
       svc.overlaps([dict(deck, tally=True, tally_offset=0), dict(deck, id="d2", tally=True, tally_offset=0)]), [])


# ------------------------------------------------------------------------------------------------ the rename
with section("The rename from atem: settings, switch and network route move to blackmagic"):
    mod = _modules.get("blackmagic")
    eq("the manifest remembers the old id", (mod.manifest.id, mod.manifest.renamed_from, mod.manifest.settings_key),
       ("blackmagic", "atem", "blackmagic"))
    check("the old module folder is gone", _modules.get("atem") is None)
    cfg = {"atem": {"devices": [{"id": "main", "name": "Main", "host": "10.0.0.5"}]},
           "modules": {"atem": {"enabled": True}},
           "network": {"routes": {"atem": "mac:aa", "propresenter": "mac:bb"}, "policy": {"atem": "strict"}}}
    _modules.clean_config(cfg)
    check("the settings moved", "atem" not in cfg and cfg["blackmagic"]["devices"][0]["host"] == "10.0.0.5")
    eq("the device became a switcher", cfg["blackmagic"]["devices"][0]["kind"], "atem")
    eq("the switch moved", (cfg["modules"]["blackmagic"], "atem" in cfg["modules"]), ({"enabled": True}, False))
    eq("the network route and policy moved, others stay", (cfg["network"]["routes"], cfg["network"]["policy"]),
       ({"propresenter": "mac:bb", "blackmagic": "mac:aa"}, {"blackmagic": "strict"}))
    cfg2 = {"atem": {"devices": [{"id": "old", "host": "10.0.0.1"}]}, "blackmagic": {"devices": [{"id": "new", "host": "10.0.0.2"}]},
            "modules": {"atem": {"enabled": True}, "blackmagic": {"enabled": False}},
            "network": {"routes": {"atem": "mac:aa", "blackmagic": "mac:cc"}}}
    _modules.clean_config(cfg2)
    eq("what is already under the new id wins", ([d["id"] for d in cfg2["blackmagic"]["devices"]],
       cfg2["modules"]["blackmagic"], cfg2["network"]["routes"]), (["new"], {"enabled": False}, {"blackmagic": "mac:cc"}))
    cfg3 = {"modules": {"atem": {"enabled": False}}}
    _modules.clean_config(cfg3)
    eq("with no settings the switch still moves", (cfg3["modules"]["blackmagic"], cfg3["blackmagic"]), ({"enabled": False}, {"devices": []}))
    from app.modules import validate
    eq("a module may not be renamed from itself", validate(type(mod)(), "blackmagic"), [])


# ------------------------------------------------------------------------------------------------ the admin routes
def fake_device(cfg, client):
    dev = svc.Device(svc.clean_device(cfg), svc.service.publish)
    dev.client = client
    svc.service.devices[dev.cfg["id"]] = dev
    return dev


with section("The admin routes for a HyperDeck and a Videohub"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"blackmagic": {"enabled": True}},
                 "blackmagic": {"devices": []}}, replace=("blackmagic",))
    _modules.attach("blackmagic")
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    operator = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")
    svc.service._running = False                          # the routes' syncs must not open real clients
    B = "/api/admin/blackmagic"

    res = admin.post(B + "/devices", json={"kind": "hyperdeck", "host": "10.0.0.7"})
    deck_cfg = res.json()["devices"][0]
    eq("a HyperDeck is added with its port and a name", (res.status_code, deck_cfg["kind"], deck_cfg["port"], deck_cfg["name"]),
       (200, "hyperdeck", 9993, "HyperDeck 1"))
    check("its page state has an empty deck block until it connects", deck_cfg["hyperdeck"] == {} and not deck_cfg["connected"])
    res = admin.post(B + "/devices", json={"kind": "videohub", "host": "10.0.0.8", "name": "Router"})
    hub_cfg = res.json()["devices"][1]
    eq("a Videohub is added", (hub_cfg["kind"], hub_cfg["port"], hub_cfg["name"], hub_cfg["presets"]), ("videohub", 9990, "Router", []))
    eq("the same address and kind is a repeat", admin.post(B + "/devices", json={"kind": "hyperdeck", "host": "10.0.0.7"}).status_code, 409)
    eq("the same address as another kind is fine (a Constellation's router)",
       admin.post(B + "/devices", json={"kind": "atem", "host": "10.0.0.7"}).status_code, 200)
    eq("a kind that does not exist is a 400", admin.post(B + "/devices", json={"kind": "mixer", "host": "10.0.0.9"}).status_code, 400)
    res = admin.put(f"{B}/devices/{hub_cfg['id']}", json={"tally": True, "port": 9991})
    eq("a Videohub takes no tally setting but a port", (res.status_code, "tally" in res.json()["devices"][1], res.json()["devices"][1]["port"]),
       (200, False, 9991))
    admin.put(f"{B}/devices/{hub_cfg['id']}", json={"port": 9990})
    eq("a switcher added after them gets camera 1", [d for d in admin.get(B).json()["devices"] if d["kind"] == "atem"][0]["tally_offset"], 0)

    # The HyperDeck: a connected client whose sends are recorded.
    dc = hd.HyperDeckClient("10.0.0.7")
    rec_deck = Sent(dc)
    dc.consume(GREETING + TRANSPORT + SLOT1 + SLOT2 + CLIPS)
    fake_device(deck_cfg, dc)
    snap = [d for d in admin.get(B).json()["devices"] if d["kind"] == "hyperdeck"][0]
    h = snap["hyperdeck"]
    eq("the page sees the deck", (snap["connected"], snap["product"], h["status"], h["clip"], h["timecode"]),
       (True, "HyperDeck Studio Mini", "stopped", {"id": 2, "name": "Take two.mov"}, "00:00:10:00"))
    eq("slots and clips", ([(s["id"], s["seconds_left"]) for s in h["slots"]], len(h["clips"])), ([(1, 3600), (2, 0)], 2))
    rec_deck.sent.clear()
    eq("a viewer cannot record", viewer.post(f"{B}/hyperdeck/record", json={"device": deck_cfg["id"]}).status_code, 403)
    eq("an operator records", operator.post(f"{B}/hyperdeck/record", json={"device": deck_cfg["id"]}).status_code, 200)
    eq("it was sent", rec_deck.sent, ["record\r\n"])
    eq("stop", admin.post(f"{B}/hyperdeck/stop", json={"device": deck_cfg["id"]}).status_code, 200)
    eq("play", admin.post(f"{B}/hyperdeck/play", json={"device": deck_cfg["id"]}).status_code, 200)
    eq("a deck must be named", admin.post(f"{B}/hyperdeck/record", json={}).status_code, 404)
    eq("a Videohub is not a deck", admin.post(f"{B}/hyperdeck/record", json={"device": hub_cfg["id"]}).status_code, 404)
    eq("an action the deck lacks", admin.post(f"{B}/hyperdeck/eject", json={"device": deck_cfg["id"]}).status_code, 404)
    dc.connected = False
    eq("a deck that is not connected is a 409", admin.post(f"{B}/hyperdeck/record", json={"device": deck_cfg["id"]}).status_code, 409)
    dc.connected = True

    # The Videohub.
    hc = vh.VideohubClient("10.0.0.8")
    rec_hub = Sent(hc)
    hc._send = lambda text: (rec_hub.sent.append(text), True)[1]
    hc.consume(prelude(locks={2: "L"}))
    fake_device(hub_cfg, hc)
    snap = [d for d in admin.get(B).json()["devices"] if d["kind"] == "videohub"][0]
    h = snap["videohub"]
    eq("the page sees the router", (snap["connected"], snap["product"], len(h["inputs"]), len(h["outputs"])),
       (True, "Blackmagic Smart Videohub 20 x 20", 3, 3))
    eq("outputs: label, source, lock", [(o["label"], o["input"], o["lock"]) for o in h["outputs"]],
       [("Main", 0, "U"), ("Overflow", 1, "U"), ("Stream", 2, "L")])
    eq("a viewer cannot route", viewer.post(f"{B}/videohub/route", json={"device": hub_cfg["id"], "output": 0, "input": 1}).status_code, 403)
    res = operator.post(f"{B}/videohub/route", json={"device": hub_cfg["id"], "output": 0, "input": 2})
    eq("an operator routes", (res.status_code, rec_hub.sent[-1]), (200, "VIDEO OUTPUT ROUTING:\n0 2\n\n"))
    from app import channels as core_channels  # noqa: E402

    out0 = next(c for c in core_channels.listing("video") if any(e["key"] == f"{hub_cfg['id']}:vout0" for e in c["ends"]))
    v = core_routing.status(out0["ref"])
    eq("the route is a core route; the router has not said it took it yet, so a difference is shown",
       (v["state"], [x["name"] for x in v["wanted"]], [x["name"] for x in v["actual"]]), ("differs", ["Slides"], ["Camera 1"]))
    hc.consume(hub_block("VIDEO OUTPUT ROUTING:", "0 2"))
    eq("once the router says so, they agree and it is what feeds the output",
       (core_routing.status(out0["ref"])["state"], [x["name"] for x in core_routing.feeds(out0["ref"])]), ("same", ["Slides"]))
    hc.consume(hub_block("VIDEO OUTPUT ROUTING:", "0 0"))
    eq("an input that does not exist", admin.post(f"{B}/videohub/route", json={"device": hub_cfg["id"], "output": 0, "input": 3}).status_code, 400)
    eq("an output that does not exist", admin.post(f"{B}/videohub/route", json={"device": hub_cfg["id"], "output": 9, "input": 0}).status_code, 400)
    sent_before = len(rec_hub.sent)
    res = admin.post(f"{B}/videohub/route", json={"device": hub_cfg["id"], "output": 2, "input": 0})
    eq("an output another surface locked is refused, and nothing is sent", (res.status_code, len(rec_hub.sent)), (409, sent_before))
    eq("a bool is not a number", admin.post(f"{B}/videohub/route", json={"device": hub_cfg["id"], "output": True, "input": 0}).status_code, 400)

    eq("an operator cannot rename", operator.post(f"{B}/videohub/labels", json={"device": hub_cfg["id"], "kind": "output", "labels": {"0": "X"}}).status_code, 403)
    res = admin.post(f"{B}/videohub/labels", json={"device": hub_cfg["id"], "kind": "output", "labels": {"0": "Main hall", "1": "Over\x07flow"}})
    eq("an admin renames outputs", (res.status_code, rec_hub.sent[-1]), (200, "OUTPUT LABELS:\n0 Main hall\n1 Overflow\n\n"))
    eq("a label for a port that does not exist", admin.post(f"{B}/videohub/labels", json={"device": hub_cfg["id"], "kind": "input", "labels": {"7": "X"}}).status_code, 400)
    eq("an empty label", admin.post(f"{B}/videohub/labels", json={"device": hub_cfg["id"], "kind": "input", "labels": {"0": "  "}}).status_code, 400)
    eq("a kind that is neither", admin.post(f"{B}/videohub/labels", json={"device": hub_cfg["id"], "kind": "serial", "labels": {"0": "X"}}).status_code, 400)

    # Renaming through core's one flow (#543): the Videohub takes the label; the channel then shows the router's name.
    from app import channels as core_channels  # noqa: E402
    admin.get(B)
    inp = next(c for c in core_channels.listing("video") if any(e["key"] == f"{hub_cfg['id']}:vin2" for e in c["ends"]))
    eq("the page is told a Videohub input can be renamed on the device", inp["can_device"], True)
    url = f"/api/admin/network/channels/{inp['id']}/rename"
    eq("an operator cannot rename", operator.post(url, json={"name": "X", "device": True}).status_code, 403)
    res = admin.post(url, json={"name": "Camera  3", "device": True})
    eq("renaming input 3 on the device sends the label", (res.status_code, rec_hub.sent[-1]), (200, "INPUT LABELS:\n2 Camera 3\n\n"))
    again = core_channels.get(inp["id"])
    eq("Relay shows the router's name and keeps no Relay-only one", (again["shown"], again["name"], again["gear_name"]),
       ("Camera 3", "", "Camera 3"))
    sent_before = len(rec_hub.sent)
    res = admin.post(url, json={"name": "", "device": True})
    eq("an empty name is refused and nothing is sent", (res.status_code, len(rec_hub.sent)), (404, sent_before))
    hc.connected = False
    res = admin.post(url, json={"name": "Lost", "device": True})
    eq("a Videohub that is not connected is refused with Relay's name unchanged",
       (res.status_code, core_channels.get(inp["id"])["shown"]), (409, "Camera 3"))
    hc.connected = True
    res = admin.post(url, json={"name": "Wide"})
    eq("a Relay-only name sends nothing to the router", (res.status_code, len(rec_hub.sent)), (200, sent_before))
    shown = core_channels.get(inp["id"])
    eq("and the router's name stays beside it", (shown["shown"], shown["gear_name"]), ("Wide", "Camera 3"))

    eq("an operator cannot lock", operator.post(f"{B}/videohub/lock", json={"device": hub_cfg["id"], "output": 0, "action": "lock"}).status_code, 403)
    res = admin.post(f"{B}/videohub/lock", json={"device": hub_cfg["id"], "output": 0, "action": "lock"})
    eq("an admin locks an output", (res.status_code, rec_hub.sent[-1]), (200, "VIDEO OUTPUT LOCKS:\n0 O\n\n"))
    res = admin.post(f"{B}/videohub/lock", json={"device": hub_cfg["id"], "output": 0, "action": "unlock"})
    eq("and unlocks it", (res.status_code, rec_hub.sent[-1]), (200, "VIDEO OUTPUT LOCKS:\n0 U\n\n"))
    eq("another surface's lock is not forced", admin.post(f"{B}/videohub/lock", json={"device": hub_cfg["id"], "output": 2, "action": "unlock"}).status_code, 409)
    eq("a lock action that does not exist", admin.post(f"{B}/videohub/lock", json={"device": hub_cfg["id"], "output": 0, "action": "force"}).status_code, 400)

    # Presets.
    hc.consume(hub_block("VIDEO OUTPUT LOCKS:", "2 U"))
    eq("an operator cannot save a preset", operator.post(f"{B}/videohub/presets", json={"device": hub_cfg["id"], "name": "A"}).status_code, 403)
    eq("a preset needs a name", admin.post(f"{B}/videohub/presets", json={"device": hub_cfg["id"], "name": " "}).status_code, 400)
    res = admin.post(f"{B}/videohub/presets", json={"device": hub_cfg["id"], "name": "Program to overflow"})
    presets = [d for d in res.json()["devices"] if d["kind"] == "videohub"][0]["presets"]
    saved = core_routing.salvos("blackmagic:" + hub_cfg["id"])
    eq("the current routing is saved as a core preset", (res.status_code, [p["name"] for p in presets],
                                                          [(len(v["routes"]), v["scope"]) for v in saved]),
       (200, ["Program to overflow"], [(3, "blackmagic:" + hub_cfg["id"])]))
    pid = presets[0]["id"]
    res = admin.post(f"{B}/videohub/presets", json={"device": hub_cfg["id"], "name": "program TO overflow"})
    again = [d for d in res.json()["devices"] if d["kind"] == "videohub"][0]["presets"]
    eq("the same name (any case) replaces it", ([p["id"] for p in again], len(again)), ([pid], 1))
    hc.consume(hub_block("VIDEO OUTPUT ROUTING:", "0 2", "1 2"))
    res = admin.post(f"{B}/videohub/presets", json={"device": hub_cfg["id"], "name": "All slides"})
    eq("a second preset", len([d for d in res.json()["devices"] if d["kind"] == "videohub"][0]["presets"]), 2)
    hc.consume(hub_block("VIDEO OUTPUT LOCKS:", "1 L"))
    rec_hub.sent.clear()
    res = operator.post(f"{B}/videohub/presets/apply", json={"device": hub_cfg["id"], "preset": pid})
    eq("an operator recalls one; the output another surface holds is left out",
       (res.status_code, res.json()["sent"], res.json()["left_out"], rec_hub.sent), (200, 2, 1, ["VIDEO OUTPUT ROUTING:\n0 0\n\n", "VIDEO OUTPUT ROUTING:\n2 2\n\n"]))
    eq("a preset that is not saved", admin.post(f"{B}/videohub/presets/apply", json={"device": hub_cfg["id"], "preset": "nope"}).status_code, 404)
    hc.consume(hub_block("VIDEO OUTPUT LOCKS:", "0 L", "1 L", "2 L"))
    eq("every output locked: nothing to send is a 409", admin.post(f"{B}/videohub/presets/apply", json={"device": hub_cfg["id"], "preset": pid}).status_code, 409)
    eq("an operator cannot delete a preset", operator.delete(f"{B}/videohub/presets/{hub_cfg['id']}/{pid}").status_code, 403)
    res = admin.delete(f"{B}/videohub/presets/{hub_cfg['id']}/{pid}")
    eq("an admin deletes one", (res.status_code, len([d for d in res.json()["devices"] if d["kind"] == "videohub"][0]["presets"])), (200, 1))
    eq("deleting it twice", admin.delete(f"{B}/videohub/presets/{hub_cfg['id']}/{pid}").status_code, 404)
    eq("a Videohub's presets survive a settings change",
       len(admin.put(f"{B}/devices/{hub_cfg['id']}", json={"name": "Router 2"}).json()["devices"][1]["presets"]), 1)
    eq("a switcher is not a Videohub", admin.post(f"{B}/videohub/route", json={"output": 0, "input": 0}).status_code, 404)
    hc.connected = False
    eq("a router that is not connected is a 409", admin.post(f"{B}/videohub/lock", json={"device": hub_cfg["id"], "output": 0, "action": "lock"}).status_code, 409)

with section("Videohub presets saved before #544 become core presets"):
    config.save({svc.KEY: {"devices": [{"id": "legacyhb", "kind": "videohub", "name": "Old hub", "host": "10.0.0.9",
                                         "presets": [{"id": "p1", "name": "Sunday", "routes": {"0": 1, "1": 0}}]}]}},
                replace=(svc.KEY,))
    got = svc.devices()
    eq("the device no longer holds them", [d.get("presets") for d in got if d["id"] == "legacyhb"], [[]])
    moved = core_routing.salvos(svc.preset_scope("legacyhb"))
    eq("they are core presets scoped to the device", [(v["name"], len(v["routes"])) for v in moved], [("Sunday", 2)])
    svc.devices()
    eq("a second read changes nothing", len(core_routing.salvos(svc.preset_scope("legacyhb"))), 1)

with section("Health and the connection test count every kind"):
    svc.service.devices.clear()
    items = svc.devices()
    fake_device(items[0], hd.HyperDeckClient("10.0.0.7"))
    h = _modules.get("blackmagic").health()
    check("a device that is not connected is a warning naming it", h.state in ("warn", "error") and items[0]["name"] in h.detail)
    check("the network map names each with its kind",
          [d["tags"][0] for d in _modules.get("blackmagic").gear_list()["devices"]] == [KIND for KIND in
           [{"hyperdeck": "HyperDeck", "videohub": "Videohub", "atem": "ATEM"}[d["kind"]] for d in svc.devices()]])

with section("Switched off"):
    asyncio.run(svc.service.stop())
    config.save({"modules": {"blackmagic": {"enabled": False}}})
    _modules.get("blackmagic").detach()
    eq("the routes answer 404", admin.get("/api/admin/blackmagic").status_code, 404)
    eq("the old routes are gone", admin.get("/api/admin/atem").status_code, 404)

done()
