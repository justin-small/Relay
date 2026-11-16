"""ATEM module (#243): the wire format, a client against a fake switcher on localhost UDP (handshake, packets
arriving out of order, tally, reliable sends, a switcher that goes quiet), publishing into the shared tally state, and
the admin routes with the module on and off. No real switcher: the fake speaks the same protocol from protocol.py.
Run: python tests/test_atem.py
"""
import asyncio
import os
import struct
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
from app import config, tally  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.atem import protocol as p  # noqa: E402
from app.modules.atem import service as svc  # noqa: E402
from app.modules.atem.client import AtemClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


def name20(text):
    return text.encode().ljust(20, b"\0")


def input_cmd(iid, long, short):
    return p.command("InPr", struct.pack(">H", iid) + name20(long) + short.encode().ljust(4, b"\0") + bytes(12))


def tally_cmd(items):
    data = struct.pack(">H", len(items)) + b"".join(struct.pack(">HB", s, f) for s, f in items)
    return p.command("TlSr", data)


print("packets and commands")
raw = p.packet(p.ACK_REQUEST, 0x1234, packet_id=7, payload=p.cut(0))
head = p.parse_header(raw)
eq("a packet round-trips", (head["flags"], head["session"], head["id"], head["payload"]),
   (p.ACK_REQUEST, 0x1234, 7, p.cut(0)))
eq("the hello is a 20-byte new-session packet", (len(p.HELLO), p.parse_header(p.HELLO)["flags"]), (20, p.NEW_SESSION))
try:
    p.parse_header(raw[:-1])
    eq("a short packet is refused", "parsed", "refused")
except p.Malformed:
    eq("a short packet is refused", 1, 1)
two = p.cut(1) + p.macro_run(3)
eq("commands split", [n for n, _ in p.split_commands(two)], ["DCut", "MAct"])
eq("a bad command length ends the list", p.split_commands(two + b"\x00\x02xxxxxxxx"), p.split_commands(two))
eq("a macro run is slot, action", p.split_commands(p.macro_run(3))[0][1], struct.pack(">HBB", 3, 0, 0))
eq("stop addresses no slot", p.split_commands(p.macro_stop())[0][1][:2], b"\xff\xff")
eq("set program is me, source", p.split_commands(p.set_program(1, 4))[0][1], bytes([1, 0, 0, 4]))
eq("old auto key has the keyer first", p.split_commands(p.downstream_auto(1, 0))[0][1][0], 1)
eq("new auto key has a flag then the keyer", p.split_commands(p.downstream_auto(1, p.V8_0_1))[0][1][:2], bytes([0, 1]))
eq("input parse", p.parse_input(input_cmd(2, "Pastor", "PAS")[8:]), {"id": 2, "long": "Pastor", "short": "PAS"})
eq("tally parse", p.parse_tally(tally_cmd([(1, 1), (2, 2), (3, 3)])[8:]),
   {1: (True, False), 2: (False, True), 3: (True, True)})
try:
    p.parse_tally(struct.pack(">H", 5))
    eq("a tally that lies about its count is refused", "parsed", "refused")
except p.Malformed:
    eq("a tally that lies about its count is refused", 1, 1)
eq("macro parse", p.parse_macro(p.command("MPrp", struct.pack(">HBBHH", 2, 1, 0, 5, 0) + b"Hello")[8:]),
   {"slot": 2, "used": True, "name": "Hello"})
eq("topology", p.parse_topology(bytes([2, 20, 4, 3])), {"mix_effects": 2, "downstream_keyers": 4})


class FakeSwitcher(asyncio.DatagramProtocol):
    """Enough of an ATEM: answers hello, dumps its state (the second packet first), acknowledges commands."""

    def __init__(self):
        self.session = 0x4321
        self.got = []           # commands the client sent
        self.acks = []          # ids the client acknowledged
        self.client = None
        self.hellos = 0
        self.answer = True
        self.next_id = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if not self.answer:
            return
        self.client = addr
        head = p.parse_header(data)
        if head["flags"] & p.NEW_SESSION:
            self.hellos += 1
            self.next_id = 0
            self.transport.sendto(p.packet(p.NEW_SESSION, self.session, packet_id=0), addr)
            return
        if head["flags"] & p.ACK_REPLY:
            self.acks.append(head["ack"])
        if head["flags"] & p.ACK_REQUEST:
            self.got.extend(p.split_commands(head["payload"]))
            self.transport.sendto(p.packet(p.ACK_REPLY, self.session, ack=head["id"]), addr)

    def push(self, *cmds, pid=None, flags=p.ACK_REQUEST):
        self.next_id = pid if pid is not None else self.next_id + 1
        self.transport.sendto(p.packet(flags, self.session, packet_id=self.next_id, payload=b"".join(cmds)), self.client)

    def dump(self):
        first = [p.command("_ver", struct.pack(">I", p.V8_0_1)),
                 p.command("_pin", b"ATEM Mini Pro".ljust(44, b"\0")),
                 p.command("_top", bytes([1, 20, 2, 3])),
                 input_cmd(1, "Wide", "WID"), input_cmd(2, "Pastor", "PAS"), input_cmd(3, "Choir", "CHR"),
                 input_cmd(1000, "Black", "BLK")]
        second = [tally_cmd([(1, 1), (2, 2), (1000, 0)]),
                  p.command("PrgI", bytes([0, 0, 0, 1])), p.command("PrvI", bytes([0, 0, 0, 2])),
                  p.command("MPrp", struct.pack(">HBBHH", 0, 1, 0, 4, 0) + b"Open"),
                  p.command("InCm", bytes(4))]
        # The second packet arrives first: the client must hold it until the first is in.
        self.push(*second, pid=2)
        self.push(*first, pid=1)
        self.next_id = 2


async def wait_for(cond, seconds=3.0):
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def run_client():
    loop = asyncio.get_running_loop()
    sw = FakeSwitcher()
    transport, _ = await loop.create_datagram_endpoint(lambda: sw, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    changes = []
    client = AtemClient("127.0.0.1", port, on_change=lambda: changes.append(1))
    client.start()
    check("the client says hello and is accepted", await wait_for(lambda: client.connected))
    sw.dump()
    check("state arrives", await wait_for(lambda: client.state.ready))
    s = client.state
    eq("the product", s.product, "ATEM Mini Pro")
    eq("input names", {i: v["long"] for i, v in s.inputs.items()}, {1: "Wide", 2: "Pastor", 3: "Choir", 1000: "Black"})
    eq("tally", s.tally[1], (True, False))
    eq("program and preview of the mix effect", (s.program[0], s.preview[0]), (1, 2))
    eq("macros", s.macros, {0: "Open"})
    eq("keyers", s.downstream_keyers, 2)
    eq("cameras are the physical inputs, with tally", client.cameras(), [
        {"n": 1, "name": "Wide", "program": True, "preview": False},
        {"n": 2, "name": "Pastor", "program": False, "preview": True},
        {"n": 3, "name": "Choir", "program": False, "preview": False}])
    check("the client acknowledged both packets", await wait_for(lambda: 2 in sw.acks))
    check("a change was reported", len(changes) > 0)

    # a change from the switcher
    sw.push(tally_cmd([(1, 0), (2, 1), (3, 2)]))
    check("a tally change arrives", await wait_for(lambda: client.cameras()[1]["program"]))
    eq("preview moved", [c["preview"] for c in client.cameras()], [False, False, True])
    # a repeat of something already taken is acknowledged, not applied twice
    before = len(changes)
    sw.push(tally_cmd([(1, 1)]), pid=sw.next_id)
    await asyncio.sleep(0.1)
    eq("a repeated packet changes nothing", len(changes), before)

    # control
    check("a cut is sent", client.send(p.cut(0)))
    check("and arrives", await wait_for(lambda: ("DCut", bytes([0, 0, 0, 0])) in sw.got))
    check("a take of source 3 to preview", client.send(p.set_preview(0, 3)))
    check("and arrives too", await wait_for(lambda: ("CPvI", bytes([0, 0, 0, 3])) in sw.got))
    check("the send queue empties once acknowledged", await wait_for(lambda: not client._inflight))

    # a lost packet is sent again
    sw.answer = False
    check("a command is queued while the switcher is deaf", client.send(p.auto(0)))
    await asyncio.sleep(0.25)
    check("and is resent", client._inflight and client._inflight[0]["tries"] >= 1)
    sw.answer = True
    check("it gets through once the switcher answers", await wait_for(lambda: ("DAut", bytes([0, 0, 0, 0])) in sw.got))

    # the switcher goes quiet for good: the client gives up and starts again
    from app.modules.atem import client as client_mod
    client_mod.QUIET_S = 0.4
    sw.answer = False
    check("a quiet switcher is dropped", await wait_for(lambda: not client.connected, 3.0))
    check("with a reason", client.error != "")
    eq("and nothing is sent while it is down", client.send(p.cut(0)), False)
    sw.answer = True
    check("it says hello again", await wait_for(lambda: sw.hellos >= 2, 5.0))
    check("and reconnects", await wait_for(lambda: client.connected, 5.0),
          (client.error, sw.hellos, sw.client, client_mod.QUIET_S))
    await client.close()
    eq("closed", client.connected, False)
    transport.close()


asyncio.run(run_client())

print("publishing into the shared tally state")
tally.tally.reset()


class FakeClient:
    """Stands in for the real client in the service: what the routes talk to."""

    def __init__(self):
        self.connected = True
        self.error = ""
        self.sent = []
        self.state = type("S", (), {"product": "ATEM", "version": p.V8_0_1, "mix_effects": 2, "downstream_keyers": 2,
                                    "inputs": {1: {"long": "Wide", "short": "WID"}, 2: {"long": "", "short": "PAS"}},
                                    "program": {0: 1}, "preview": {0: 2}, "macros": {0: "Open"}})()

    def send(self, *cmds):
        if not self.connected:
            return False
        self.sent.extend(cmds)
        return True

    async def close(self):
        self.connected = False

    def cameras(self):
        return [{"n": 1, "name": "Wide", "program": True, "preview": False}]


svc.service.client = FakeClient()
svc.service.publish()
eq("the camera is published", [c["name"] for c in tally.tally.cameras()], ["Wide"])
svc.service.client.connected = False
svc.service.publish()
eq("a lost switcher leaves nothing", tally.tally.cameras(), [])
svc.service.client.connected = True

print("settings")
eq("an address is kept", svc.clean_settings({"host": " 10.0.0.5 ", "port": 9910}), {"host": "10.0.0.5", "port": 9910})
eq("junk is dropped", svc.clean_settings({"host": "a b/c", "port": "x"}), {"host": "", "port": 9910})
eq("a bad port falls back", svc.clean_settings({"host": "sw.local", "port": 70000})["port"], 9910)

print("the admin routes")
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"atem": {"enabled": True}}})
_modules.attach("atem")
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")

res = admin.get("/api/admin/atem")
eq("the state", (res.status_code, res.json()["connected"], res.json()["product"]), (200, True, "ATEM"))
eq("inputs are named, an unnamed one by its short name", [i["name"] for i in res.json()["inputs"]], ["Wide", "PAS"])
eq("each mix effect says what is on", res.json()["mix_effects"][0], {"me": 0, "program": 1, "preview": 2})
eq("macros", res.json()["macros"], [{"slot": 0, "name": "Open"}])
eq("a viewer can look", viewer.get("/api/admin/atem").status_code, 200)
eq("a viewer cannot cut", viewer.post("/api/admin/atem/cut", json={"me": 0}).status_code, 403)
eq("an operator cuts", operator.post("/api/admin/atem/cut", json={"me": 0}).status_code, 200)
eq("an operator cannot change the address",
   operator.put("/api/admin/atem/settings", json={"host": "10.0.0.9"}).status_code, 403)
eq("the cut was sent", svc.service.client.sent[-1], p.cut(0))
eq("auto", admin.post("/api/admin/atem/auto", json={"me": 1}).status_code, 200)
eq("auto was sent for that mix effect", svc.service.client.sent[-1], p.auto(1))
eq("a mix effect that does not exist", admin.post("/api/admin/atem/cut", json={"me": 5}).status_code, 400)
eq("fade to black", admin.post("/api/admin/atem/fade-to-black", json={"me": 0}).status_code, 200)
eq("program", admin.post("/api/admin/atem/program", json={"me": 0, "source": 2}).status_code, 200)
eq("sent", svc.service.client.sent[-1], p.set_program(0, 2))
eq("preview", admin.post("/api/admin/atem/preview", json={"me": 0, "source": 1}).status_code, 200)
eq("a source that is not a number", admin.post("/api/admin/atem/preview", json={"me": 0, "source": "x"}).status_code, 400)
eq("a downstream key goes on air", admin.post("/api/admin/atem/downstream-key", json={"keyer": 1, "action": "on"}).status_code, 200)
eq("sent as on air", svc.service.client.sent[-1], p.downstream_on_air(1, True))
eq("a downstream key mixes", admin.post("/api/admin/atem/downstream-key", json={"keyer": 0, "action": "auto"}).status_code, 200)
eq("sent as auto for this version", svc.service.client.sent[-1], p.downstream_auto(0, p.V8_0_1))
eq("a key that does not exist", admin.post("/api/admin/atem/downstream-key", json={"keyer": 9, "action": "on"}).status_code, 400)
eq("a macro runs", admin.post("/api/admin/atem/macro", json={"slot": 0, "action": "run"}).status_code, 200)
eq("sent", svc.service.client.sent[-1], p.macro_run(0))
eq("a macro stops", admin.post("/api/admin/atem/macro", json={"action": "stop"}).status_code, 200)
svc.service.client.connected = False
res = admin.post("/api/admin/atem/cut", json={"me": 0})
eq("with no switcher a cut says so", (res.status_code, "not connected" in res.json()["error"]), (409, True))
res = admin.put("/api/admin/atem/settings", json={"host": "no spaces/please"})
eq("a bad address is a 400", res.status_code, 400)

print("switched off")
asyncio.run(svc.service.stop())
config.save({"modules": {"atem": {"enabled": False}}})
_modules.get("atem").detach()
eq("the routes answer 404", admin.get("/api/admin/atem").status_code, 404)
eq("and the tally is gone", tally.tally.cameras(), [])

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
