"""Panel registry: check-in, the event stream, offline timing, limits, admin control.

Offline: no OpenAI, no hardware. Run: .venv/bin/python tests/test_panels.py
"""
import asyncio
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from fastapi.testclient import TestClient

from app import config, main, panels
from app.main import app

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


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = TestClient(app, base_url="http://relay.local:8001", headers={"x-admin-token": "t0ken"})
reg = panels.registry

clock = [1000.0]
panels._now = lambda: clock[0]


def pid(n: int) -> str:
    return f"{n:032x}"


def body(n: int = 1, **over) -> dict:
    out = {"id": pid(n), "name": f"Hall {n}", "version": "0.1.0", "scripts": ["latin"],
           "feeds": [{"stream": "translation", "lang": "SPANISH"}], "ip": "192.168.1.50",
           "uptime_s": 42, "rssi": -58}
    out.update(over)
    return out


print("\ncheck-in")
r = viewer.post("/api/panels/checkin", json=body())
eq("a check-in on the viewer port needs no login", r.status_code, 200)
eq("the reply names the panel and the interval",
   (r.json()["name"], r.json()["interval_s"]), ("Hall 1", 30))
rows = admin.get("/api/admin/panels").json()["panels"]
eq("the panel is listed, online", [(p["id"], p["online"]) for p in rows], [(pid(1), True)])
row = rows[0]
eq("it carries what the panel said",
   (row["version"], row["scripts"], row["feeds"], row["ip"], row["uptime_s"], row["rssi"]),
   ("0.1.0", ["latin"], [{"stream": "translation", "lang": "SPANISH"}], "192.168.1.50", 42, -58))
check("a check-in alone writes nothing to config.json", config.get()["panels"] == {})
r = viewer.post("/api/panels/checkin", json={"id": pid(2)})
eq("a bare check-in is enough", r.status_code, 200)
eq("an unnamed panel gets a stand-in name", r.json()["name"], "Panel 0000")
r = viewer.post("/api/panels/checkin", json=body(3, ip="not an ip", rssi="loud", scripts=["latin", "<b>", 7]))
eq("odd fields are dropped, not refused", r.status_code, 200)
row = next(p for p in admin.get("/api/admin/panels").json()["panels"] if p["id"] == pid(3))
eq("a bad ip falls back to the peer's", row["ip"], "testclient")
eq("a bad rssi is dropped", row["rssi"], None)
eq("bad scripts are dropped", row["scripts"], ["latin"])

print("\nwhat a check-in may not do")
eq("a bad id is refused", viewer.post("/api/panels/checkin", json=body(id="nope")).status_code, 400)
eq("an id with capitals is refused", viewer.post("/api/panels/checkin", json=body(id="A" * 32)).status_code, 400)
eq("a non-object body is refused", viewer.post("/api/panels/checkin", json=[1]).status_code, 400)
eq("a non-JSON body is refused",
   viewer.post("/api/panels/checkin", content=b"{nope", headers={"content-type": "application/json"}).status_code, 400)
big = viewer.post("/api/panels/checkin", json=body(name="x" * (panels.CHECKIN_MAX_BYTES + 1)))
eq("a body over the cap is refused", big.status_code, 413)


def chunks():
    # No Content-Length: the size has to be caught as it streams in.
    for _ in range(40):
        yield b" " * 512


eq("…even when it does not say how big it is",
   viewer.post("/api/panels/checkin", content=chunks(), headers={"content-type": "application/json"}).status_code, 413)
row = next(p for p in admin.get("/api/admin/panels").json()["panels"] if p["id"] == pid(1))
eq("a long name is cut", len(row["reported_name"]) <= 64, True)
viewer.post("/api/panels/checkin", json=body(1, name="n" * 500, version="v" * 500))
row = next(p for p in admin.get("/api/admin/panels").json()["panels"] if p["id"] == pid(1))
eq("name and version are bounded", (len(row["reported_name"]), len(row["version"])), (64, 32))

print("\noffline after 90 s")
viewer.post("/api/panels/checkin", json=body(1))
clock[0] += 89
check("89 s on, still online", reg.view(pid(1))["online"])
clock[0] += 2
check("91 s on, offline", not reg.view(pid(1))["online"])
eq("an offline panel stays listed",
   pid(1) in [p["id"] for p in admin.get("/api/admin/panels").json()["panels"]], True)
viewer.post("/api/panels/checkin", json=body(1))
check("the next check-in brings it back", reg.view(pid(1))["online"])
flags = [p["online"] for p in admin.get("/api/admin/panels").json()["panels"]]
eq("online panels list before offline ones", flags, sorted(flags, reverse=True))

print("\nlimits")
for n in range(100, 100 + panels.MAX_PANELS):
    viewer.post("/api/panels/checkin", json=body(n))
check("the registry never passes its cap", len(reg.panels) <= panels.MAX_PANELS, len(reg.panels))
r = viewer.post("/api/panels/checkin", json=body(999))
eq("a new panel while all are online is refused", r.status_code, 429)
check("…and holds no memory", pid(999) not in reg.panels)
clock[0] += 200
r = viewer.post("/api/panels/checkin", json=body(999))
eq("once panels are offline, the stalest makes room", r.status_code, 200)
check("…and the cap still holds", len(reg.panels) <= panels.MAX_PANELS)
reg.panels.clear()

print("\nadmin control")
viewer.post("/api/panels/checkin", json=body(1))
r = admin.post(f"/api/admin/panels/{pid(1)}", json={"name": "Lobby"})
eq("rename", (r.status_code, r.json()["name"]), (200, "Lobby"))
eq("the new name is persisted", config.get()["panels"][pid(1)]["name"], "Lobby")
eq("the next check-in reply uses it",
   viewer.post("/api/panels/checkin", json=body(1)).json()["name"], "Lobby")
r = admin.post(f"/api/admin/panels/{pid(1)}", json={"feeds": [{"stream": "translation", "lang": "french"}]})
eq("set a feed", (r.status_code, r.json()["set_feeds"]), (200, [{"stream": "translation", "lang": "FRENCH"}]))
eq("…persisted with a revision",
   (config.get()["panels"][pid(1)]["feeds"], config.get()["panels"][pid(1)]["feed_rev"]),
   ([{"stream": "translation", "lang": "FRENCH"}], 1))
for bad, why in (({"feeds": [{"stream": "translation", "lang": "KLINGON"}]}, "unknown language"),
                 ({"feeds": [{"stream": "nope"}]}, "unknown stream"),
                 ({"feeds": []}, "empty"),
                 ({"feeds": [{"stream": "transcription"}] * 3}, "three feeds"),
                 ({"feeds": "transcription"}, "not a list"),
                 ({"name": 5}, "name not a string"),
                 ({"feeds": [{"stream": "translation", "lang": "JAPANESE"}]}, "a script the panel cannot draw")):
    eq(f"refused: {why}", admin.post(f"/api/admin/panels/{pid(1)}", json=bad).status_code, 400)
eq("an unknown panel is 404", admin.post(f"/api/admin/panels/{pid(77)}", json={"name": "x"}).status_code, 404)
eq("a malformed id is 404", admin.post("/api/admin/panels/short", json={"name": "x"}).status_code, 404)
r = admin.post(f"/api/admin/panels/{pid(1)}", json={"feeds": [{"stream": "transcription"}, {"stream": "translation", "lang": "SPANISH"}]})
eq("two feeds", (r.status_code, len(r.json()["set_feeds"])), (200, 2))
eq("every change takes the next revision", config.get()["panels"][pid(1)]["feed_rev"], 2)

print("\nthe operator's setting survives a restart")
reg.panels.clear()
config.load()
eq("named and fed panels are listed with no check-in",
   [(p["id"], p["name"], p["online"]) for p in reg.listing()], [(pid(1), "Lobby", False)])
config.save({"panels": {}})
check("a plain config save does not lose or invent panels", pid(1) in config.get()["panels"])

print("\nthe feed reaches a panel that has not applied it")
r = viewer.post("/api/panels/checkin", json=body(1))
eq("a check-in reply carries the pending set_feed",
   r.json().get("set_feed"), {"type": "set_feed", "rev": 2, "feeds": config.get()["panels"][pid(1)]["feeds"]})
r = viewer.post("/api/panels/checkin", json=body(1, feed_rev=2))
check("…and stops once the panel acknowledges it", "set_feed" not in r.json())
r = viewer.post("/api/panels/checkin", json=body(1, feed_rev=0))
check("…and a rebooted panel (rev 0) is not told again this run: its screen choice stands", "set_feed" not in r.json())
admin.post(f"/api/admin/panels/{pid(1)}", json={"feeds": [{"stream": "transcription"}]})
r = viewer.post("/api/panels/checkin", json=body(1, feed_rev=2))
eq("a newer operator setting is sent", r.json().get("set_feed", {}).get("rev"), 3)
admin.post(f"/api/admin/panels/{pid(1)}", json={"feeds": None})
check("clearing the feed leaves nothing to send",
      "set_feed" not in viewer.post("/api/panels/checkin", json=body(1)).json())
eq("clearing keeps the name", config.get()["panels"][pid(1)].get("name"), "Lobby")

print("\nforget")
eq("forget", admin.delete(f"/api/admin/panels/{pid(1)}").status_code, 200)
check("it leaves config.json", pid(1) not in config.get()["panels"])
check("and the list", pid(1) not in [p["id"] for p in reg.listing()])
eq("forgetting twice is 404", admin.delete(f"/api/admin/panels/{pid(1)}").status_code, 404)

print("\nadmin routes stay on the admin port")
viewer.post("/api/panels/checkin", json=body(1))
for method, path in (("get", "/api/admin/panels"), ("post", f"/api/admin/panels/{pid(1)}"),
                     ("delete", f"/api/admin/panels/{pid(1)}")):
    r = getattr(viewer, method)(path, headers={"x-admin-token": "t0ken"})
    eq(f"viewer port: {method.upper()} {path} is 404", r.status_code, 404)
r = TestClient(app, base_url="http://relay.local:8001").get("/api/admin/panels")
eq("admin routes still need the token", r.status_code, 401)
eq("panel routes are not served on the admin port's behalf",
   TestClient(app, base_url="http://relay.local:8001").post("/api/panels/checkin", json=body(5)).status_code, 200)

print("\nthe event stream, in process")


async def stream_in_process():
    reg.panels.clear()
    reg.panels[pid(1)] = panels.Panel(pid(1), last_seen=clock[0])
    q = reg.subscribe(pid(1))
    check("nothing pending, nothing sent", q.empty())
    reg.update(pid(1), feeds=[{"stream": "transcription"}])
    frame = q.get_nowait()
    eq("set_feed is pushed at once", json.loads(frame[len(b"data: "):]),
       {"type": "set_feed", "rev": 1, "feeds": [{"stream": "transcription"}]})
    eq("pending feed is sent on connect",
       json.loads(reg.subscribe(pid(1)).get_nowait()[6:])["rev"], 1)
    eq("two streams per panel", len(reg._subs[pid(1)]), 2)
    third = reg.subscribe(pid(1))
    third.get_nowait()  # the pending set_feed it is primed with
    eq("a third closes the oldest", len(reg._subs[pid(1)]), 2)
    eq("…which is told to stop", q.get_nowait() is None or q.get_nowait() is None, True)
    n = reg.push_update_available({"version": "0.2.0", "size": 1234, "evil": {"x": 1}}, pid(1))
    eq("update_available goes to the open streams", n, 2)
    msg = json.loads(third.get_nowait()[6:])
    eq("…with scalars only", msg, {"type": "update_available", "version": "0.2.0", "size": 1234})
    for _ in range(panels.EVENT_QUEUE_MAX + 5):
        reg._push(pid(1), {"type": "set_feed"})
    check("a reader that never reads is cut, not buffered forever", third.qsize() <= panels.EVENT_QUEUE_MAX)
    reg.forget(pid(1))
    eq("forgetting a panel closes its streams", reg.stream_count(), 0)


asyncio.run(stream_in_process())

print("\nthe event stream, over a socket")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


import uvicorn

main.HEARTBEAT_S = 0.3
port = free_port()
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
check("a server is up", server.started)


def conn() -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", port, timeout=5)


def read_frame(resp) -> str:
    """One SSE frame, as text, off a chunked response."""
    buf = b""
    while not buf.endswith(b"\n\n"):
        line = resp.readline()
        if not line:
            break
        buf += line
    return buf.decode()


c = conn()
c.request("GET", f"/api/panels/{pid(9)}/events")
eq("a stream for a panel that never checked in is 404", c.getresponse().status, 404)
c.close()
c = conn()
c.request("GET", "/api/panels/not-an-id/events")
eq("…and for a malformed id", c.getresponse().status, 404)
c.close()

# The server sees the viewer port as `port`; the admin routes are open here (single socket),
# so set the config to match for what follows.
config.save({"port": port, "admin_port": port})
c = conn()
c.request("POST", "/api/panels/checkin", json.dumps(body(9, feed_rev=0)), {"content-type": "application/json"})
eq("check in over a socket", c.getresponse().status, 200)
c.close()
ev = conn()
ev.request("GET", f"/api/panels/{pid(9)}/events")
resp = ev.getresponse()
eq("the stream opens", resp.status, 200)
eq("as an event stream", resp.getheader("content-type"), "text/event-stream; charset=utf-8")
frame = read_frame(resp)
check("a quiet stream pings", frame.startswith(": ping"), frame)
c = conn()
c.request("POST", f"/api/admin/panels/{pid(9)}", json.dumps({"feeds": [{"stream": "translation", "lang": "PORTUGUESE"}]}),
          {"content-type": "application/json", "x-admin-token": "t0ken"})
eq("the operator sets a feed", c.getresponse().status, 200)
c.close()
for _ in range(10):
    frame = read_frame(resp)
    if frame.startswith("data:"):
        break
msg = json.loads(frame[len("data: "):])
eq("it arrives on the open stream, not at the next check-in", (msg["type"], msg["feeds"]),
   ("set_feed", [{"stream": "translation", "lang": "PORTUGUESE"}]))
ev.close()
for _ in range(50):
    if reg.stream_count() == 0:
        break
    time.sleep(0.1)
eq("a dropped client's stream is released", reg.stream_count(), 0)
server.should_exit = True

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
