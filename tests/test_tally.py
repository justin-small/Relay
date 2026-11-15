"""The shared tally state and the tally screens (#242, #245, #248): publishing from more than one source, merging,
clearing, the feed's frames, and the /tally page in each view. Run: python tests/test_tally.py
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

import app.main as main  # noqa: E402,F401
from app import config, tally  # noqa: E402
from app.main import app  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


def cam(n, name="", program=False, preview=False):
    return {"n": n, "name": name, "program": program, "preview": preview}


T = tally.tally
T.reset()

print("publishing")
eq("empty", (T.cameras(), T.payload()["active"]), ([], False))
T.publish("atem", [cam(2, "Pastor", preview=True), cam(1, "Wide", program=True)])
eq("cameras come sorted by number", [c["n"] for c in T.cameras()], [1, 2])
p = T.payload()
eq("live and next", (p["live"], p["next"], p["active"]), ([1], [2], True))
T.publish("x", [cam(5, "")])
eq("an unnamed camera is named by its number", [c["name"] for c in T.cameras() if c["n"] == 5], ["Camera 5"])
T.clear("x")
T.publish("atem", [cam(0), cam(1000), cam(True), {"n": "1"}, "junk", None, cam(3, "  A   B  ")])
eq("unusable cameras are dropped and names tidied", [(c["n"], c["name"]) for c in T.cameras()], [(3, "A B")])
T.publish("atem", [cam(1, "Wide", True), cam(1, "Again", False, True)])
eq("the first of a repeated number wins", [(c["name"], c["program"], c["preview"]) for c in T.cameras()],
   [("Wide", True, False)])
T.publish("Bad Id!", [cam(9)])
eq("a source that is not a module id is ignored", [c["n"] for c in T.cameras()], [1])
T.publish("atem", [cam(n, f"C{n}") for n in range(1, 200)])
eq("at most 64 cameras", len(T.cameras()), tally.MAX_CAMERAS)
T.publish("atem", [cam(1, "x" * 100)])
eq("a name is cut", len(T.cameras()[0]["name"]), tally.NAME_MAX)

print("two sources")
T.reset()
T.publish("atem", [cam(1, "Wide", program=True), cam(2, "Pastor")])
T.publish("tally_arbiter", [cam(1, "Other name"), cam(2, "Pastor", preview=True), cam(3, "Choir", preview=True)])
eq("program and preview are merged, the first name wins",
   [(c["n"], c["name"], c["program"], c["preview"]) for c in T.cameras()],
   [(1, "Wide", True, False), (2, "Pastor", False, True), (3, "Choir", False, True)])
T.clear("atem")
eq("clearing one source leaves the other", [(c["n"], c["program"]) for c in T.cameras()],
   [(1, False), (2, False), (3, False)])
v = T.version()
T.clear("atem")
eq("clearing what is not there changes nothing", T.version(), v)
T.publish("tally_arbiter", T.cameras())
eq("publishing the same thing changes nothing", T.version(), v)
T.reset()

print("the feed")


def data(frame):
    return json.loads(frame.decode().split("data: ", 1)[1])


async def frames_test():
    T.reset()
    T.publish("atem", [cam(1, "Wide", program=True)])
    gen = tally.frames(poll_s=0.02, tick_s=0.5)
    first = data(await gen.__anext__())
    eq("the first frame is the whole state", (first["type"], first["live"], first["cameras"][0]["name"]),
       ("tally", [1], "Wide"))
    t = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.15)
    check("nothing is sent while nothing changes", not t.done())
    T.publish("atem", [cam(1, "Wide", preview=True)])
    second = data(await asyncio.wait_for(t, 1))
    eq("a change is sent at once", (second["live"], second["next"]), ([], [1]))
    third = data(await asyncio.wait_for(gen.__anext__(), 2))
    eq("the state is sent again on the tick, so a screen never goes quiet", third["next"], [1])
    await gen.aclose()


asyncio.run(frames_test())
check("the signature ignores the clock",
      tally.signature({"a": 1, "server_time": 1}) == tally.signature({"a": 1, "server_time": 2}))
T.reset()

print("the screens")
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
from fastapi.testclient import TestClient  # noqa: E402

viewer = TestClient(app, base_url="http://relay.local:8000")
res = viewer.get("/tally?camera=2")
eq("a camera screen answers on the viewer port with no login", res.status_code, 200)
check("it is the camera view for camera 2", 'data-view="camera"' in res.text and 'data-camera="2"' in res.text)
check("it reads the tally feed", "tally.js" in res.text)
res = viewer.get("/tally?view=operator&camera=3")
check("the operator view", 'data-view="operator"' in res.text and 'data-camera="3"' in res.text)
check("with a live and a next slot", 'id="opLive"' in res.text and 'id="opNext"' in res.text)
res = viewer.get("/tally?view=board")
check("the board for the director", 'data-view="board"' in res.text and 'id="board"' in res.text)
check("the board offers USB cameras", 'id="usbAll"' in res.text)
check("a view that does not exist is the camera view", 'data-view="camera"' in viewer.get("/tally?view=evil").text)
check("a camera that is not a number is none", 'data-camera="0"' in viewer.get("/tally?camera=abc").text)
check("a huge camera number is capped", 'data-camera="999"' in viewer.get("/tally?camera=99999999").text)
check("markup in the address is not echoed", "<script>" not in viewer.get('/tally?view="><script>&camera=1').text)
check("the script is served", viewer.get("/static/tally.js").status_code == 200)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
