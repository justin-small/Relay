"""NDI multiview (#297): layouts, cleaning saved multiviews, the composed picture (sources, clock, timer, tally,
meter, lost signal), sharing one composer between browsers, and the routes. A fake NDI backend stands in for the SDK.
Run: python tests/test_ndi_multiview.py
"""
import asyncio
import io
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
from PIL import Image  # noqa: E402

from app import config, tally  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.ndi import MODULE, multiview, runtime, viewer  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

A, B = "CAM (Stage)", "CAM (Wide)"


class Conn:
    def __init__(self, colour):
        self.colour = colour

    def receive(self, timeout_ms):
        time.sleep(0.01)
        return {"width": 64, "height": 36, "fps": 30.0, "fourcc": "BGRA", "data": bytes(self.colour) * (64 * 36)}

    def close(self):
        pass


class Backend:
    def __init__(self):
        self.opened = []

    def connect(self, name, address=""):
        self.opened.append(name)
        return Conn((0, 0, 255, 255) if name == A else (0, 255, 0, 255))   # BGRA: red, then green


with section("layouts"):
    eq("2x2 has four boxes", len(multiview.cells("2x2")), 4)
    eq("3x3 has nine", len(multiview.cells("3x3")), 9)
    eq("4x4 has sixteen", len(multiview.cells("4x4")), 16)
    eq("1 big and 5 small is six boxes, the big one first", (len(multiview.cells("1+5")), multiview.cells("1+5")[0]), (6, (0, 0, 2, 2)))
    eq("1 big and 7 small is eight", len(multiview.cells("1+7")), 8)
    cs = multiview.cells("1+5")
    check("no two boxes overlap", all(not (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                                      for i, a in enumerate(cs) for b in cs[i + 1:]))

with section("saved multiviews"):
    eq("no name is no multiview", multiview.clean_multiview({"layout": "2x2"}), None)
    m = multiview.clean_multiview({"name": "  Booth   wall ", "layout": "nope", "boxes": [{"kind": "source", "source": A, "meter": True, "bad": 1}] * 9})
    eq("a bad layout falls back, boxes are cut to the layout", (m["name"], m["layout"], len(m["boxes"])), ("Booth wall", "2x2", 4))
    eq("a short list is padded with empty boxes", [b["kind"] for b in multiview.clean_multiview({"name": "x", "layout": "2x2", "boxes": [{"kind": "clock"}]})["boxes"]], ["clock", "empty", "empty", "empty"])
    eq("a bad time falls back", multiview.clean_box({"kind": "timer", "at": "25:99"})["at"], "10:30")
    got = multiview.clean_multiviews([{"name": "Stage"}, {"name": "Stage"}, {"name": ""}, "x", {"name": "Wall", "id": "BAD ID"}])
    eq("ids are unique and made from names", [g["id"] for g in got], ["stage", "stage-2", "wall"])
    eq("at most 20", len(multiview.clean_multiviews([{"name": f"m{i}"} for i in range(40)])), 20)

with section("timer text"):
    eq("a long way off", multiview.countdown_text(3725), "1:02:05")
    eq("past is Starting", multiview.countdown_text(-5), "Starting")
    now = time.time()
    t = multiview.timer_target(time.strftime("%H:%M", time.localtime(now - 120)), now)
    check("a time that has passed means tomorrow", 23 * 3600 < t - now < 24 * 3600 + 60)

with section("the composed picture"):
    runtime.install(Backend())
    s = svc.service
    asyncio.run(s.start())
    for n in (A, B):
        s.merge([{"name": n, "machine": "CAM", "source": n[5:-1], "host": "", "address": "10.0.0.5", "port": 0, "groups": [], "converter": None}])
    MODULE.attach()
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"ndi": {"enabled": True}},
                 "ndi": {**s.settings(), "view_width": 640, "view_fps": 20, "tally": {A: 4},
                         "multiviews": [{"name": "Wall", "layout": "2x2", "boxes": [
                             {"kind": "source", "source": A, "label": "Stage", "meter": True},
                             {"kind": "source", "source": B},
                             {"kind": "clock"}, {"kind": "timer", "mode": "time", "at": "10:30"}]}]}})
    mid = s.settings()["multiviews"][0]["id"]
    eq("saved with an id", mid, "wall")

    async def watch():
        a = await asyncio.to_thread(s.multiviews.open, mid)
        b = await asyncio.to_thread(s.multiviews.open, mid)
        check("the same composer for two browsers", a is b and s.multiviews.count() == {mid: 2})
        eq("one receiver per source", sorted(s.pool.counts()), [A, B])
        await asyncio.sleep(0.4)
        got = await a.next(0, 3)
        check("a JPEG arrives", got is not None and got[1][:2] == b"\xff\xd8")
        img = Image.open(io.BytesIO(got[1])).convert("RGB")
        eq("the size follows view_width", img.size, (640, 360))
        rects = a.rects
        centre = lambda r: img.getpixel((r[0] + r[2] // 2, r[1] + r[3] // 2))
        c0, c1 = centre(rects[0]), centre(rects[1])
        check("box 1 shows the red source", c0[0] > 150 and c0[1] < 80)
        check("box 2 shows the green source", c1[1] > 150 and c1[0] < 80)
        s._level(A, -6.0)
        tally.tally.publish("obs_t", [{"n": 4, "name": "Stage", "program": True, "preview": False}])
        await asyncio.sleep(0.3)
        got2 = await a.next(got[0], 3)
        img2 = Image.open(io.BytesIO(got2[1])).convert("RGB")
        edge = img2.getpixel((rects[0][0] + 2, rects[0][1] + rects[0][3] // 2))
        check("a program camera gets a red border", edge[0] > 180 and edge[1] < 90)
        tally.tally.clear("obs_t")
        await asyncio.to_thread(s.multiviews.close, a)
        eq("still open for the other browser", s.multiviews.count(), {mid: 1})
        await asyncio.to_thread(s.multiviews.close, b)
        check("every receiver is released with the last", wait_for(lambda: s.pool.counts() == {}))
        eq("nothing is left open", s.multiviews.count(), {})
        eq("an unknown multiview", await asyncio.to_thread(s.multiviews.open, "nope"), None)

    asyncio.run(watch())

    async def lost():
        c = await asyncio.to_thread(s.multiviews.open, mid)
        await asyncio.sleep(0.3)
        c._tiles.clear()
        c._tile_last = {i: time.monotonic() + 60 for i in range(4)}      # no new frames
        jpeg = c.compose()
        check("a source with no frames still composes", jpeg[:2] == b"\xff\xd8")
        await asyncio.to_thread(s.multiviews.close, c)

    asyncio.run(lost())

with section("the routes"):
    admin = _db.client(app)
    viewer_role = _db.client(app, "viewer", username="watcher")
    anon = TestClient(app, base_url="http://relay.local:8001")
    on_viewer_port = TestClient(app, base_url="http://relay.local:8000")
    eq("the page", admin.get("/ndi/multiview/wall").status_code, 200)
    eq("a viewer role may open it", viewer_role.get("/ndi/multiview/wall").status_code, 200)
    eq("an unknown one is 404", admin.get("/ndi/multiview/nope").status_code, 404)
    eq("signed out cannot stream", anon.get("/ndi/multiview/wall/stream").status_code, 401)
    eq("not on the viewer port", on_viewer_port.get("/ndi/multiview/wall").status_code, 404)

    async def route():
        from app.modules.ndi import routes

        resp = await routes.multiview_stream("wall")
        eq("the stream", (resp.status_code, resp.media_type.split(";")[0]), (200, "multipart/x-mixed-replace"))
        first = await resp.body_iterator.__anext__()
        check("it starts with a part", first.startswith(f"--{viewer.BOUNDARY}".encode()))
        await resp.body_iterator.aclose()
        check("leaving closes it", wait_for(lambda: s.multiviews.count() == {}))
        eq("unknown is 404", (await routes.multiview_stream("nope")).status_code, 404)

    asyncio.run(route())
    r = admin.put("/api/admin/ndi/settings", json={"multiviews": [{"name": "Two", "layout": "3x3", "boxes": []}]})
    eq("saving multiviews", (r.status_code, [m["id"] for m in r.json()["settings"]["multiviews"]]), (200, ["two"]))
    eq("a non-list is refused", admin.put("/api/admin/ndi/settings", json={"multiviews": "x"}).status_code, 400)
    eq("a viewer cannot save", viewer_role.put("/api/admin/ndi/settings", json={"multiviews": []}).status_code, 403)

with section("the next service time"):
    eq("nothing knows it", registry.service_starts_at(), None)
    from app import modules as core

    core.provide("plan.starts_at", lambda: 123.0, owner="planning_center")      # what Planning Center provides
    eq("the module that knows answers", registry.service_starts_at(), 123.0)
    core.withdraw("plan.starts_at")
    eq("and nothing does once it is gone", registry.service_starts_at(), None)

MODULE.detach()
asyncio.run(svc.service.stop())
runtime.reset()
done()
