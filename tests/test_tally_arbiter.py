"""Tally Arbiter module (#244): the Socket.IO frames, turning devices and bus states into cameras, a listener against
a fake Tally Arbiter server over a real local websocket, publishing into the shared tally state, and the admin routes
with the module on and off. Run: python tests/test_tally_arbiter.py
"""
import asyncio
import json
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

import websockets  # noqa: E402

import app.main as main  # noqa: E402,F401
from app import config, tally  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.tally_arbiter import listener as ta  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


print("frames")
eq("an event is encoded", ta.encode_event("devices"), '42["devices"]')
eq("with arguments", ta.encode_event("x", {"a": 1}), '42["x",{"a":1}]')
eq("open", ta.decode('0{"sid":"abc","pingInterval":25000}'), ("open", {"sid": "abc", "pingInterval": 25000}))
eq("ping", ta.decode("2"), ("ping", None))
eq("connect", ta.decode('40{"sid":"x"}'), ("connect", None))
eq("an event", ta.decode('42["device_states",[{"busId":"b"}]]'), ("event", ("device_states", [[{"busId": "b"}]])))
eq("an event with a namespace", ta.decode('42/n,["x",1]'), ("event", ("x", [1])))
eq("junk is other", ta.decode("42not json")[0], "other")
eq("empty is other", ta.decode("")[0], "other")

print("cameras")
BUSES = [{"id": "pv", "type": "preview"}, {"id": "pg", "type": "program"}]
DEVS = [{"id": "d1", "name": "Wide"}, {"id": "d2", "name": "Pastor"}, {"id": "d3"}, {"name": "no id"}, "junk"]
eq("devices become numbered cameras", ta.cameras_from(DEVS, BUSES, {"d1": {"pg"}, "d2": {"pv"}}), [
    {"n": 1, "name": "Wide", "program": True, "preview": False},
    {"n": 2, "name": "Pastor", "program": False, "preview": True},
    {"n": 3, "name": "", "program": False, "preview": False}])
eq("both busses at once", ta.cameras_from(DEVS[:1], BUSES, {"d1": {"pg", "pv"}})[0]["program"], True)
eq("settings", ta.clean_settings({"host": " ta.local ", "port": 4455}), {"host": "ta.local", "port": 4455})
eq("junk settings", ta.clean_settings({"host": "a/b", "port": 0}), {"host": "", "port": 4455})


class Fake:
    """A Tally Arbiter: Engine.IO open, accepts the namespace, answers devices and bus_options and listener joins."""

    def __init__(self):
        self.seen = []
        self.states = {"d1": {"pg"}, "d2": set()}
        self.sockets = []
        self.deaf = False

    def devices(self):
        return [{"id": "d1", "name": "Wide"}, {"id": "d2", "name": "Pastor"}]

    def state_for(self, did):
        return [{"busId": b, "deviceId": did, "sources": ["s"] if b in self.states[did] else []}
                for b in ("pv", "pg")]

    async def handler(self, ws):
        self.sockets.append(ws)
        await ws.send('0{"sid":"abc","pingInterval":25000,"pingTimeout":20000}')
        async for frame in ws:
            if self.deaf:
                continue
            kind, value = ta.decode(frame)
            if kind == "connect":
                await ws.send('40{"sid":"xyz"}')
                await ws.send("2")
            elif kind == "event":
                name, args = value
                self.seen.append((name, args))
                if name == "devices":
                    await ws.send(ta.encode_event("devices", self.devices()))
                elif name == "bus_options":
                    await ws.send(ta.encode_event("bus_options", BUSES))
                elif name == "listenerclient_connect":
                    await ws.send(ta.encode_event("device_states", self.state_for(args[0]["deviceId"])))
            elif kind == "pong":
                self.seen.append(("pong", []))

    async def push(self, did):
        for ws in self.sockets:
            await ws.send(ta.encode_event("device_states", self.state_for(did)))


async def wait_for(cond, seconds=4.0):
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def run_listener():
    fake = Fake()
    server = await websockets.serve(fake.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    changes = []
    li = ta.Listener("127.0.0.1", port, on_change=lambda: changes.append(1))
    li.start()
    check("the listener connects", await wait_for(lambda: li.connected))
    check("it answers a ping", await wait_for(lambda: ("pong", []) in fake.seen))
    check("it learns the cameras and their tally", await wait_for(
        lambda: [c["name"] for c in li.cameras()] == ["Wide", "Pastor"] and li.cameras()[0]["program"]))
    eq("the busses and states", [(c["program"], c["preview"]) for c in li.cameras()], [(True, False), (False, False)])
    eq("it joined each device as a listener", sorted(a[0]["deviceId"] for n, a in fake.seen if n == "listenerclient_connect"),
       ["d1", "d2"])
    eq("as a listener that cannot be flashed or reassigned",
       [a[0]["canBeFlashed"] or a[0]["canBeReassigned"] for n, a in fake.seen if n == "listenerclient_connect"], [False, False])
    # a change from the server
    fake.states["d1"], fake.states["d2"] = set(), {"pg"}
    await fake.push("d1")
    await fake.push("d2")
    check("a tally change arrives", await wait_for(lambda: li.cameras()[1]["program"] and not li.cameras()[0]["program"]))
    check("changes were reported", len(changes) > 0)
    # the connection drops: the listener says so and comes back
    for ws in list(fake.sockets):
        await ws.close()
    check("a dropped server is noticed", await wait_for(lambda: not li.connected))
    eq("and the cameras are forgotten", li.cameras(), [])
    check("it reconnects", await wait_for(lambda: li.connected, 6.0))
    await li.close()
    eq("closed", li.connected, False)
    server.close()
    await server.wait_closed()


asyncio.run(run_listener())

print("publishing")
tally.tally.reset()


class FakeListener:
    connected = True
    error = ""

    def cameras(self):
        return [{"n": 1, "name": "Wide", "program": False, "preview": True}]


ta.service.listener = FakeListener()
ta.service.publish()
eq("published", [c["preview"] for c in tally.tally.cameras()], [True])
ta.service.listener.connected = False
ta.service.publish()
eq("a lost server leaves nothing", tally.tally.cameras(), [])
ta.service.listener = None

print("the admin routes")
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"tally_arbiter": {"enabled": True}}})
_modules.attach("tally_arbiter")
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
res = admin.get("/api/admin/tally-arbiter")
eq("not set up", (res.status_code, res.json()["configured"], res.json()["connected"]), (200, False, False))
eq("a viewer can look", viewer.get("/api/admin/tally-arbiter").status_code, 200)
eq("a viewer cannot change the address",
   viewer.put("/api/admin/tally-arbiter/settings", json={"host": "10.0.0.5"}).status_code, 403)
eq("a bad address is a 400", admin.put("/api/admin/tally-arbiter/settings", json={"host": "a b/c"}).status_code, 400)
eq("the module says it is not set up", _modules.get("tally_arbiter").health().state, "warn")

print("switched off")
config.save({"modules": {"tally_arbiter": {"enabled": False}}})
_modules.get("tally_arbiter").detach()
eq("the routes answer 404", admin.get("/api/admin/tally-arbiter").status_code, 404)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
