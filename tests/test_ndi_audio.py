"""NDI audio (#301): per-channel levels and loudness on the service, audio-only sources, listening in a browser, NDI sound
on the 32-lane bus (an input and an output), and a routed output that takes its sound from another source. A fake NDI
backend stands in for the SDK; the mixing and the PCM maths run in Rust for real.
Run: python tests/test_ndi_audio.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import numpy as np  # noqa: E402

from app import audio, config  # noqa: E402
from app.main import app  # noqa: E402
from app import modules as registry  # noqa: E402
from app.modules.ndi import MODULE, bus, listen, runtime  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

SRC, ONLY = "CAM (Stage)", "MIC (Pulpit)"
RATE = 48000


def chunk(db_left=-20.0, db_right=-40.0, n=480):
    x = np.sin(2 * np.pi * 1000 * np.arange(n) / RATE).astype(np.float32)
    left, right = x * 10 ** (db_left / 20), x * 10 ** (db_right / 20)
    return {"audio": np.stack([left, right]), "rate": RATE, "audio_db": round(db_left, 1),
            "levels": [[db_left, db_left - 3.0], [db_right, db_right - 3.0]]}


class Conn:
    def __init__(self, backend, name):
        self.backend, self.name, self.n = backend, name, 0

    def receive(self, timeout_ms):
        time.sleep(0.01)  # allow-sleep: paces the fake sender like a real wire
        self.n += 1
        if self.name == ONLY or self.n % 2:
            return chunk()
        return {"width": 64, "height": 36, "fps": 30.0, "fourcc": "BGRA", "data": bytes(64 * 36 * 4)}

    def close(self):
        self.backend.closed.append(self.name)


class Sink:
    def __init__(self, name, channels, rate):
        self.name, self.channels, self.rate, self.wrote, self.closed, self.underruns = name, channels, rate, [], False, 0

    def free(self):
        return 9600 - sum(len(b) for b in self.wrote)

    def queued(self):
        return sum(len(b) for b in self.wrote)

    def write(self, block):
        self.wrote.append(np.array(block))
        return len(block)

    def close(self):
        self.closed = True


class Combined:
    def __init__(self, name, video, audio):
        self.name, self.video, self.audio, self.closed = name, video, audio, False

    def close(self):
        self.closed = True


class Backend:
    def __init__(self):
        self.opened, self.closed, self.sinks, self.combined, self.routers = [], [], [], [], []

    def connect(self, name, address="", low=False):
        self.opened.append((name, low))
        return Conn(self, name)

    def open_audio_sink(self, name, channels=2, rate=48000):
        s = Sink(name, channels, rate)
        self.sinks.append(s)
        return s

    def open_combiner(self, name, video="", audio=""):
        c = Combined(name, video, audio)
        self.combined.append(c)
        return c

    def open_router(self, name):
        r = type("R", (), {"name": name, "target": None, "closed": False,
                           "change": lambda self, s, a="": setattr(self, "target", s) or True,
                           "clear": lambda self: setattr(self, "target", None) or True,
                           "close": lambda self: setattr(self, "closed", True)})()
        self.routers.append(r)
        return r


def found(name, machine, source):
    return {"name": name, "machine": machine, "source": source, "host": "", "address": "10.0.0.5", "port": 0,
            "groups": [], "converter": None}


with section("choosing channels"):
    eq("nothing chosen means all", listen.parse_channels(""), [])
    eq("channels count from 1", listen.parse_channels("1,2"), [0, 1])
    eq("a repeat is kept once", listen.parse_channels("3,3,1"), [2, 0])
    for bad in ("0", "a", "1,,2", "65", "-1"):
        eq(f"{bad!r} is refused", listen.parse_channels(bad), None)

with section("mixing to PCM for a browser"):
    c = chunk(-6.0, -120.0, 480)
    left = np.frombuffer(listen.pcm(c, [0]), dtype="<i2").reshape(-1, 2)
    eq("a frame is two samples of two bytes", left.shape, (480, 2))
    check("one channel plays on both sides", (left[:, 0] == left[:, 1]).all())
    check("at its level: -6 dBFS peaks near 16384", 16000 < int(np.abs(left).max()) < 16600)
    both = np.frombuffer(listen.pcm(c, []), dtype="<i2").reshape(-1, 2)
    check("all channels: left is the first, right the second", np.abs(both[:, 1]).max() < 4 and np.abs(both[:, 0]).max() > 10000)
    eq("no sound gives nothing", listen.pcm({"audio": None}, []), b"")

with section("levels, loudness and audio-only sources on the service"):
    backend = Backend()
    runtime.install(backend)
    s = svc.service
    asyncio.run(s.start())
    s.merge([found(SRC, "CAM", "Stage"), found(ONLY, "MIC", "Pulpit")])
    MODULE.attach()
    s.audio_only_after = 0.3
    lease = s.hook_subscribe(SRC, lambda f: None)
    only = s.hook_subscribe(ONLY, lambda f: None)
    check("channels have levels", wait_for(lambda: s.audio_levels(SRC) == [-20.0, -40.0]))
    first = s.audio_state(SRC)
    eq("each channel's peak and rms", first["channels"], [{"peak": -20.0, "rms": -23.0}, {"peak": -40.0, "rms": -43.0}])
    check("loudness runs once a viewer asks", wait_for(lambda: isinstance(s.audio_state(SRC)["momentary"], float)))
    eq("a source with pictures is not audio only", s.audio_state(SRC)["audio_only"], False)
    check("a source that sends only sound is marked audio only", wait_for(lambda: s.audio_state(ONLY)["audio_only"]))
    row = next(x for x in s.sources() if x["name"] == ONLY)
    eq("the list says so, with the channel count", (row["audio_only"], row["audio_channels"]), (True, 2))
    eq("an unknown source has no levels", s.audio_levels("nope"), None)
    lease.release()
    only.release()

with section("the level and listen routes"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"ndi": {"enabled": True}},
                 "ndi": s.settings()})
    admin = _db.client(app)
    enc = "CAM%20%28Stage%29"
    keep = s.hook_subscribe(SRC, lambda f: None)
    wait_for(lambda: s.audio_levels(SRC))
    lv = admin.get(f"/ndi/level/{enc}").json()
    eq("the level answer carries the channels", [c["peak"] for c in lv["channels"]], [-20.0, -40.0])
    eq("listening to nothing known is 404", admin.get("/ndi/listen/nothing").status_code, 404)
    eq("a bad channel list is 400", admin.get(f"/ndi/listen/{enc}?ch=zero").status_code, 400)
    viewer_role = _db.client(app, "viewer", username="listener")
    check("a viewer may open the level", viewer_role.get(f"/ndi/level/{enc}").status_code == 200)

    async def listening():
        from app.modules.ndi import routes

        resp = await routes.view_listen(SRC, "1")
        eq("the rate is in a header", (resp.headers["X-Rate"], resp.headers["X-Channels"]), ("48000", "2"))
        check("it is held on the low-bandwidth receiver", (SRC, True) in backend.opened)
        got = b""
        async for part in resp.body_iterator:
            got += part
            if len(got) >= 4 * 480 * 3:
                break
        check("the body is stereo 16-bit PCM", len(got) % 4 == 0 and len(got) >= 4 * 480 * 3)
        await resp.body_iterator.aclose()
        check("the receiver goes with the listener", wait_for(lambda: (SRC + svc.LOW) not in s.pool._entries))
        eq("and the slot is free", routes._listeners.count, 0)

    asyncio.run(listening())

    async def too_many():
        from app.modules.ndi import routes

        taken = [routes._listeners.take() for _ in range(listen.MAX_LISTENERS)]
        resp = await routes.view_listen(SRC, "")
        eq("beyond the limit is refused", resp.status_code, 503)
        for _ in taken:
            routes._listeners.give()

    asyncio.run(too_many())
    keep.release()

with section("NDI sound on the lane bus"):
    check("the module registers an NDI source and output", audio.get_source("ndi") is not None and audio.get_output("ndi") is not None)
    src = audio.get_source("ndi")
    try:
        src.open("x", 0, lambda b: None)
        ok = False
    except audio.SourceError:
        ok = True
    check("the mono path points to the bus", ok)
    inp = src.lane_input()
    blocks = []
    info = inp.open(SRC, blocks.append)
    eq("the source's channels are the lane input's", (info.device["channels"], info.rate, info.device["label"]), (2, RATE, SRC))
    check("blocks arrive as frames by channels", wait_for(lambda: blocks) and blocks[0].shape == (480, 2) and blocks[0].dtype == np.float32)
    check("it is alive while sound arrives", inp.alive())
    try:
        audio.get_source("ndi").lane_input().open("", lambda b: None)
        named = False
    except audio.SourceError:
        named = True
    check("an input needs a source name", named)
    inp.close()
    check("closing gives the receiver back", wait_for(lambda: (SRC + svc.LOW) not in s.pool._entries))
    check("it is not alive once closed", not inp.alive())

    out = audio.get_output("ndi")
    sink = out.lane_sink("Translation: Spanish", 2)
    eq("the sink is an audio-only NDI source with its own name", backend.sinks[-1].name, "Relay - Translation: Spanish")
    eq("48 kHz", sink.rate, 48000)
    taken = sink.write(np.ones((100, 2), np.float32))
    eq("the bus's block is written", (taken, sink.queued() > 0), (100, True))
    eq("the engine's free space is the sink's", sink.free(), backend.sinks[-1].free())
    sink.close()
    check("closing closes the NDI source", backend.sinks[-1].closed)
    out.play(b"\x00\x00", 24000)      # nothing Relay plays reaches NDI except through the bus: no error, no sink
    eq("playing a clip directly opens nothing", len(backend.sinks), 1)

with section("a routed output with its sound from another source"):
    from app.modules.ndi import router as rt
    from app import channels as core_channels  # noqa: F401

    r = s.router
    registry.provide("routes.ndi", r.api(), owner="ndi")
    registry.provide("gear.ndi", {"list": s.gear_list}, owner="ndi")
    add = r.add_output("Lobby TV")
    inst = backend.routers[-1]
    eq("by default the sound follows the picture (a plain NDI route)", (inst.name, r.snapshot()["routes"][0]["audio"]), ("Relay - Lobby TV", ""))
    eq("route a picture", r.route(add["id"], SRC), "")
    eq("the library was told", inst.target, SRC)
    eq("take the sound from the pulpit mic", r.set_audio(add["id"], ONLY), "")
    check("the routed instance was let go", inst.closed)
    comb = backend.combined[-1]
    eq("it is now a combiner with that picture and sound", (comb.name, comb.video, comb.audio), ("Relay - Lobby TV", SRC, ONLY))
    eq("the page shows where the sound comes from", r.snapshot()["routes"][0]["audio"], ONLY)
    eq("a change of picture restarts the combiner", r.route(add["id"], "PTZ (Cam 2)"), "")
    eq("with the new picture and the same sound", (backend.combined[-1].video, backend.combined[-1].audio), ("PTZ (Cam 2)", ONLY))
    check("the old combiner closed", comb.closed)
    eq("an output cannot take its own sound", r.set_audio(add["id"], "PC (Relay - Lobby TV)"), "An output cannot take sound from itself.")
    eq("an unknown output is refused", r.set_audio("nope1234", ONLY), "That output does not exist.")
    eq("put the sound back with the picture", r.set_audio(add["id"], ""), "")
    eq("a plain router again, pointed at the picture", backend.routers[-1].target, "PTZ (Cam 2)")
    eq("the saved choice survives a clean", rt.clean_outputs([{"id": "abcd1234", "name": "TV", "audio": "X (y)"}]),
       [{"id": "abcd1234", "name": "TV", "audio": "X (y)"}])
    r.remove_output(add["id"])

with section("the routes"):
    r2 = admin.put("/api/admin/ndi/router/outputs/nope1234/audio", json={"source": "x"})
    eq("an unknown output answers with a refusal", r2.status_code, 409)
    eq("a non-text source is refused", admin.put("/api/admin/ndi/router/outputs/nope1234/audio", json={"source": 5}).status_code, 400)
    eq("a viewer role may not change it", viewer_role.put("/api/admin/ndi/router/outputs/nope1234/audio", json={"source": ""}).status_code, 403)

asyncio.run(svc.service.stop())
MODULE.detach()
check("detaching removes the bus source and output", audio.get_source("ndi") is None and audio.get_output("ndi") is None)
done()
