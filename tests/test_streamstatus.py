"""The status feed (#224) and the shared stream status modules publish into (#249).

GET /stream?stream=status on the viewer port: the whole state in each message, a new one on a change and again
every tick, no reason text, and modules' stream entries bounded and cleaned.

Offline: no OpenAI, no hardware. Run: .venv/bin/python tests/test_streamstatus.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from fastapi.testclient import TestClient

from app import panels, streamstatus
from app.engine import engine
from app.main import app
from app.streamstatus import StreamStatus, input_state, streams

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def raises(label, fn):
    try:
        fn()
    except ValueError:
        check(label, True)
        return
    check(label, False, "no ValueError")


def unframe(frame: bytes) -> dict:
    line = [ln for ln in frame.decode().splitlines() if ln.startswith("data: ")][0]
    return json.loads(line[6:])


print("stream entries")
s = StreamStatus()
s.publish("obs", label="OBS Studio", on_air=True, health="ok", live_since=1000.0, viewers=None,
          detail="connected to 10.0.0.5:4455")
e = s.entries()[0]
eq("public entry", e, {"source": "obs", "label": "OBS Studio", "on_air": True, "health": "ok",
                       "live_since": 1000.0, "viewers": None})
check("detail never in the public entry", "detail" not in e)
eq("detail kept for admin", s.entries(admin=True)[0]["detail"], "connected to 10.0.0.5:4455")
s.publish("obs", label="OBS Studio", on_air=False, health="ok", live_since=1000.0)
eq("off air drops live_since", s.entries()[0]["live_since"], None)
s.publish("youtube", label="  YouTube\x07 live channel with a long name  ", on_air=None, viewers=12.7)
y = [x for x in s.entries() if x["source"] == "youtube"][0]
eq("label cleaned and bounded", y["label"], "YouTube live channel wit")
eq("viewers an int", y["viewers"], 12)
eq("on_air unknown stays None", y["on_air"], None)
s.publish("resi", label="Resi", viewers=True)
eq("a bool is not a viewer count", s.entries()[1]["viewers"], None)
eq("sorted by label", [x["source"] for x in s.entries()], ["obs", "resi", "youtube"])
raises("source must be a module id", lambda: s.publish("Bad Id", label="x"))
raises("health must be known", lambda: s.publish("obs", label="x", health="great"))
for i in range(streamstatus.MAX_SOURCES - 3):
    s.publish(f"m{i}", label="x")
raises("bounded number of sources", lambda: s.publish("onemore", label="x"))
s.publish("obs", label="OBS again")  # replacing one is still allowed when full
s.clear("obs")
check("clear removes it", all(x["source"] != "obs" for x in s.entries()))
s.clear("not-there")  # no error

print("input state")
eq("engine stopped is off", input_state({"running": True, "rms_dbfs": -20}, False)["state"], "off")
eq("capture not running is off", input_state({"running": False}, True)["state"], "off")
eq("reconnecting is lost", input_state({"running": True, "reconnecting": True, "rms_dbfs": -20}, True)["state"], "lost")
eq("error is lost", input_state({"running": True, "error": "x", "rms_dbfs": -20}, True)["state"], "lost")
eq("clipping", input_state({"running": True, "clipping": True, "rms_dbfs": -2}, True)["state"], "clipping")
eq("silent", input_state({"running": True, "rms_dbfs": -55.4}, True), {"state": "silent", "dbfs": -55})
eq("ok", input_state({"running": True, "rms_dbfs": -18.6}, True), {"state": "ok", "dbfs": -19})
eq("no level", input_state({}, False), {"state": "off", "dbfs": -60})

print("the message")
streams.clear("obs")
msg = streamstatus.payload(now=5000.0)
eq("type", msg["type"], "status")
eq("stopped", (msg["running"], msg["started_at"]), (False, None))
eq("no stream source: on_air unknown", msg["on_air"], None)
eq("no streams", msg["streams"], [])
eq("server_time", msg["server_time"], 5000.0)
check("viewers is a count", isinstance(msg["viewers"], int))
streams.publish("obs", label="OBS", on_air=False, health="error", detail="wrong password")
msg = streamstatus.payload()
eq("off air", msg["on_air"], False)
check("the reason never reaches the viewer port", "wrong password" not in json.dumps(msg))
streams.publish("obs", label="OBS", on_air=True, health="ok", live_since=10.0)
eq("on air when a source says so", streamstatus.payload()["on_air"], True)

print("what counts as a change")
a = streamstatus.payload(now=1.0)
b = dict(a, server_time=2.0, input=dict(a["input"], dbfs=a["input"]["dbfs"] - 3))
eq("the clock and the exact level are not a change", streamstatus.signature(a), streamstatus.signature(b))
c = dict(a, input={"state": "clipping", "dbfs": -1})
check("the input's state is", streamstatus.signature(a) != streamstatus.signature(c))
d = dict(a, viewers=a["viewers"] + 1)
check("the viewer count is", streamstatus.signature(a) != streamstatus.signature(d))


async def collect():
    out = []
    gen = streamstatus.frames(poll_s=0.01, tick_s=0.05)
    out.append(unframe(await gen.__anext__()))
    # Unchanged: nothing until the tick.
    t0 = asyncio.get_running_loop().time()
    out.append(unframe(await gen.__anext__()))
    waited = asyncio.get_running_loop().time() - t0
    streams.publish("obs", label="OBS", on_air=False, health="ok")
    t0 = asyncio.get_running_loop().time()
    out.append(unframe(await gen.__anext__()))
    changed_after = asyncio.get_running_loop().time() - t0
    await gen.aclose()
    return out, waited, changed_after

frames, waited, changed_after = asyncio.run(collect())
check("first message at once", frames[0]["type"] == "status")
check("a repeat waits for the tick", waited >= 0.04, waited)
eq("a change is sent", frames[2]["on_air"], False)
check("a change goes before the tick", changed_after < 0.04, changed_after)

print("panels and targets")
eq("status is a panel feed", panels.clean_feeds([{"stream": "status"}]), [{"stream": "status"}])
eq("status beside captions", panels.clean_feeds([{"stream": "transcription"}, {"stream": "status"}]),
   [{"stream": "transcription"}, {"stream": "status"}])
eq("targets offer it", engine.targets_payload()["status"], True)
check("admin status carries the reasons", any(x.get("detail") == "" or "detail" in x
                                              for x in engine.status()["streams"]))

print("routes")
viewer = TestClient(app, base_url="http://relay.local:8000")
r = viewer.get("/status")
eq("status page answers", r.status_code, 200)
check("page loads its script", "status.js" in r.text)
eq("caption feeds still refuse a bad stream", viewer.get("/stream?stream=bogus").status_code, 400)


async def first_frame():
    import app.main as main

    class _Req:
        query_params = {}
        headers = {}

    resp = await main.stream(_Req(), stream="status")
    check("is an event stream", resp.media_type == "text/event-stream")
    it = resp.body_iterator
    frame = await it.__anext__()
    await it.aclose()
    return unframe(frame)

first = asyncio.run(first_frame())
eq("first message is the state", first["type"], "status")
eq("with the stream entry", [x["source"] for x in first["streams"]], ["obs"])
check("no detail on the wire", all("detail" not in x for x in first["streams"]))

streams.clear("obs")
print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
