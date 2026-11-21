"""NDI single-source viewer (#296): the JPEG encoder, the shared feed, the MJPEG route, its permission and its
operator-port-only rule, the settings limits, and the picture tile. A fake NDI backend stands in for the SDK.
Run: python tests/test_ndi_viewer.py
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

from fastapi.testclient import TestClient  # noqa: E402

from app import config, tally  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.ndi import MODULE, runtime, viewer  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

SRC = "CAM (Stage)"


def bgra(w=8, h=4):
    return {"width": w, "height": h, "fps": 30.0, "fourcc": "BGRA", "data": bytes([10, 20, 200, 255]) * (w * h)}


def uyvy(w=8, h=4):
    return {"width": w, "height": h, "fps": 30.0, "fourcc": "FourCC.UYVY", "data": bytes([128, 120, 128, 120]) * (w // 2 * h)}


class Conn:
    def __init__(self, backend, name):
        self.backend = backend

    def receive(self, timeout_ms):
        time.sleep(0.01)
        return bgra(1920, 1080) if self.backend.big else bgra()

    def close(self):
        self.backend.closed += 1


class Backend:
    big = False

    def __init__(self):
        self.opened, self.closed = [], 0

    def connect(self, name, address=""):
        self.opened.append(name)
        return Conn(self, name)


with section("the encoder"):
    for name, f in (("BGRA", bgra()), ("UYVY", uyvy()), ("RGBA", {**bgra(), "fourcc": "RGBA"})):
        j = viewer.encode(f, 1280, 60)
        check(f"{name} makes a JPEG", bool(j) and j[:2] == b"\xff\xd8")
    eq("an unknown pixel format is refused", viewer.encode({**bgra(), "fourcc": "P216"}, 1280, 60), None)
    eq("an empty frame is refused", viewer.encode({**bgra(), "data": b""}, 1280, 60), None)
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(viewer.encode(bgra(1920, 1080), 640, 60)))
    eq("the width cap shrinks it, keeping the shape", img.size, (640, 360))
    y = Image.open(io.BytesIO(viewer.encode(uyvy(), 1280, 90))).convert("RGB").getpixel((2, 1))
    check("UYVY grey stays grey", max(y) - min(y) < 12)

with section("settings limits"):
    s = svc.clean_settings({"view_fps": 500, "view_quality": 1, "view_width": "wide", "view_max": 3,
                            "tally": {"A": 2, "B": 0, "C": True, "": 3, "D": "x"}})
    eq("clamped and defaulted", (s["view_fps"], s["view_quality"], s["view_width"], s["view_max"]), (30, 20, 1280, 3))
    eq("only whole camera numbers from 1 are kept", s["tally"], {"A": 2})
    eq("older settings get the defaults", svc.clean_settings({})["view_fps"], 15)

with section("the shared feed"):
    backend = Backend()
    runtime.install(backend)
    s = svc.service
    asyncio.run(s.start())
    s.merge([{"name": SRC, "machine": "CAM", "source": "Stage", "host": "", "address": "10.0.0.5", "port": 0,
              "groups": [], "converter": None}])
    MODULE.attach()

    async def watch():
        a = await asyncio.to_thread(s.viewers.open, SRC)
        b = await asyncio.to_thread(s.viewers.open, SRC)
        got = await a.next(0, 3)
        check("a frame arrives as a JPEG", got is not None and got[1][:2] == b"\xff\xd8")
        eq("two viewers, one feed", s.viewers.count(), {SRC: 2})
        eq("one receiver for both", backend.opened, [SRC])
        part = None
        async for p in viewer.mjpeg(a):
            part = p
            break
        check("the stream part is framed", part.startswith(f"--{viewer.BOUNDARY}".encode()) and b"image/jpeg" in part)
        await asyncio.to_thread(s.viewers.close, a)
        eq("still open for the other viewer", backend.closed, 0)
        await asyncio.to_thread(s.viewers.close, b)
        check("the receiver closes with the last viewer", wait_for(lambda: backend.closed == 1))
        eq("no feeds are left", s.viewers.count(), {})

    asyncio.run(watch())

    async def limits():
        config.save({"ndi": {**s.settings(), "view_max": 1}})
        a = await asyncio.to_thread(s.viewers.open, SRC)
        other = await asyncio.to_thread(s.viewers.open, "OTHER (x)")
        eq("beyond view_max is refused", other, None)
        await asyncio.to_thread(s.viewers.close, a)

    asyncio.run(limits())
    config.save({"ndi": {**s.settings(), "view_max": 4}})

    async def capped():
        """Frames arrive every 10 ms but the cap is 2 a second: far fewer than that are encoded."""
        config.save({"ndi": {**s.settings(), "view_fps": 2}})
        f = await asyncio.to_thread(s.viewers.open, SRC)
        await f.next(0, 3)
        await asyncio.sleep(0.6)
        n = f.seq
        await asyncio.to_thread(s.viewers.close, f)
        check("the frame-rate cap holds", n <= 2)

    asyncio.run(capped())
    config.save({"ndi": {**s.settings(), "view_fps": 15}})

with section("the audio level"):
    s._level(SRC, -18.2)
    eq("a fresh level", s.audio_level(SRC), -18.2)
    s._audio[SRC] = (-18.2, time.monotonic() - 10)
    eq("a stale level is none", s.audio_level(SRC), None)
    eq("an unknown source has none", s.audio_level("nope"), None)

with section("the routes"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
                 "modules": {"ndi": {"enabled": True}}, "ndi": {**s.settings(), "tally": {SRC: 3}}})
    admin = _db.client(app)
    viewer_role = _db.client(app, "viewer", username="watcher")
    anon = TestClient(app, base_url="http://relay.local:8001")
    on_viewer_port = TestClient(app, base_url="http://relay.local:8000")
    enc = "CAM%20%28Stage%29"
    r = admin.get(f"/ndi/view/{enc}")
    eq("the page", r.status_code, 200)
    check("it names the camera number for the tally border", '"camera": 3' in r.text)
    eq("a viewer role may open it", viewer_role.get(f"/ndi/view/{enc}").status_code, 200)
    check("signed out gets the sign-in, not the picture", "CAM (Stage)" not in anon.get(f"/ndi/view/{enc}").text)
    eq("signed out cannot stream", anon.get(f"/ndi/stream/{enc}").status_code, 401)
    eq("signed out cannot read the level", anon.get(f"/ndi/level/{enc}").status_code, 401)
    eq("an unknown source is 404", admin.get("/ndi/view/nothing").status_code, 404)
    eq("so is its stream", admin.get("/ndi/stream/nothing").status_code, 404)
    eq("not on the viewer port", on_viewer_port.get(f"/ndi/view/{enc}").status_code, 404)
    lv = admin.get(f"/ndi/level/{enc}").json()
    eq("the level answer", sorted(lv), ["audio_only", "channels", "db", "fps", "height", "momentary", "short", "width"])

    async def stream_route():
        # TestClient cannot hold an endless response open, so call the route and read its body directly.
        from app.modules.ndi import routes

        resp = await routes.view_stream(SRC)
        eq("the stream", (resp.status_code, resp.media_type.split(";")[0]), (200, "multipart/x-mixed-replace"))
        first = await resp.body_iterator.__anext__()
        check("it starts with a part", first.startswith(f"--{viewer.BOUNDARY}".encode()))
        await resp.body_iterator.aclose()
        check("leaving closes the receiver", wait_for(lambda: s.viewers.count() == {}))
        full = await routes.view_stream("nothing")
        eq("an unknown source", full.status_code, 404)

    asyncio.run(stream_route())

    put = lambda body: admin.put("/api/admin/ndi/settings", json=body)
    eq("fps out of range is refused", put({"view_fps": 99}).status_code, 400)
    eq("a word is refused", put({"view_quality": "high"}).status_code, 400)
    eq("a bad camera number is refused", put({"tally": {"A": 0}}).status_code, 400)
    r = put({"view_fps": 10, "view_quality": 70, "view_width": 960, "view_max": 2, "tally": {SRC: 5}})
    eq("good limits are saved", (r.status_code, r.json()["settings"]["view_fps"], r.json()["settings"]["tally"]),
       (200, 10, {SRC: 5}))

with section("the picture tile"):
    types = [w["type"] for w in MODULE.board_widgets()]
    check("it is offered", "ndi.view" in types)
    eq("drawn by core's frame kind", [w["kind"] for w in MODULE.board_widgets() if w["type"] == "ndi.view"], ["frame"])
    items = [{"id": "p", "type": "ndi.view", "props": {"source": SRC}}, {"id": "q", "type": "ndi.view", "props": {}},
             {"id": "r", "type": "ndi.view", "props": {"source": "gone"}}]
    d = asyncio.run(MODULE.board_data(items))
    eq("the address", d["p"]["image"], "/ndi/stream/CAM%20%28Stage%29")
    eq("no tally while nothing is on air", d["p"]["tally"], "")
    tally.tally.publish("obs_test", [{"n": 5, "name": "Stage", "program": True, "preview": False}])
    eq("red when its camera is on air", asyncio.run(MODULE.board_data(items))["p"]["tally"], "program")
    tally.tally.publish("obs_test", [{"n": 5, "name": "Stage", "program": False, "preview": True}])
    eq("green on preview", asyncio.run(MODULE.board_data(items))["p"]["tally"], "preview")
    tally.tally.clear("obs_test")
    eq("nothing chosen", (d["q"]["image"], d["q"]["note"]), ("", "Choose a source"))
    eq("not found", (d["r"]["image"], d["r"]["note"]), ("", "Not found"))
    s.merge([])
    s.merge([])
    eq("an offline source shows no picture", asyncio.run(MODULE.board_data(items))["p"]["note"], "Offline")

MODULE.detach()
asyncio.run(svc.service.stop())
runtime.reset()
done()
