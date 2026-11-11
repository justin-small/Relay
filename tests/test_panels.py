"""Panel registry: check-in, the event stream, offline timing, limits, admin control.

Offline: no OpenAI, no hardware. Run: .venv/bin/python tests/test_panels.py
"""
import asyncio
import hashlib
import http.client
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from fastapi.testclient import TestClient

from app import config, languages, main, panelfw, panels
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

print("\noperator panel: list view, live frames, feed provenance")
seen_frames = []
reg.on_change = lambda: seen_frames.append(main._panels_frame())
viewer.post("/api/panels/checkin", json=body(20, scripts=["latin"]))
check("a check-in tells the admin stream", len(seen_frames) == 1 and seen_frames[-1]["type"] == "panels")
row = next(p for p in seen_frames[-1]["panels"] if p["id"] == pid(20))
labels = [l["id"] for l in row["languages"]]
check("languages the panel cannot draw are not offered",
      "SPANISH" in labels and "RUSSIAN" not in labels, labels)
viewer.post("/api/panels/checkin", json=body(21, scripts=[]))
row = next(p for p in main._panels_frame()["panels"] if p["id"] == pid(21))
check("a panel that has not reported scripts is offered every language",
      {l["id"] for l in row["languages"]} == set(languages.TARGETS))
row = next(p for p in main._panels_frame()["panels"] if p["id"] == pid(20))
eq("a first check-in is not a feed change", (row["feeds_changed_by"], row["feeds_changed_ago_s"]), (None, None))

viewer.post("/api/panels/checkin", json=body(20, feeds=[{"stream": "transcription"}]))
row = next(p for p in main._panels_frame()["panels"] if p["id"] == pid(20))
eq("a feed that changed with no operator revision is marked as changed at the screen",
   row["feeds_changed_by"], "screen")
check("and says how long ago", row["feeds_changed_ago_s"] is not None and row["feeds_changed_ago_s"] < 5)

n = len(seen_frames)
r = admin.post(f"/api/admin/panels/{pid(20)}", json={"feeds": [{"stream": "translation", "lang": "FRENCH"}]})
eq("the operator sets a feed", r.status_code, 200)
check("setting a feed tells the admin stream", len(seen_frames) == n + 1)
r = admin.post(f"/api/admin/panels/{pid(20)}", json={"feeds": [{"stream": "translation", "lang": "RUSSIAN"}]})
eq("a language the panel cannot draw is refused", r.status_code, 400)
rev = next(p for p in main._panels_frame()["panels"] if p["id"] == pid(20))["set_feeds"]
viewer.post("/api/panels/checkin", json=body(20, feeds=[{"stream": "translation", "lang": "FRENCH"}], feed_rev=1))
row = next(p for p in main._panels_frame()["panels"] if p["id"] == pid(20))
eq("a change that applies the operator's revision is marked as from admin", row["feeds_changed_by"], "admin")

n = len(seen_frames)
r = admin.delete(f"/api/admin/panels/{pid(20)}")
eq("the operator forgets a panel", r.status_code, 200)
check("forgetting tells the admin stream", len(seen_frames) == n + 1)
check("and it is gone from the list", pid(20) not in [p["id"] for p in main._panels_frame()["panels"]])

before = reg.online_signature()
clock[0] += panels.OFFLINE_AFTER_S + 1
check("a panel going quiet changes the online signature without any push",
      reg.online_signature() != before)
reg.on_change = lambda: main.hub.push_status(main._panels_frame())

# ---------------------------------------------------------------------------
# Firmware updates: the image Relay holds, and the panel's side of the exchange.
# ---------------------------------------------------------------------------
print("\nfirmware: the image check")


def make_image(version="0.2.0", project="relay-display-app", chip=0x0009, signed=True, filler=3000):
    """An application image shaped like a built and signed one: header, one segment that opens with the
    application descriptor, the image's own checksum and SHA-256, and a Secure Boot V2 block whose
    digest and CRC are the real ones. Not a runnable image; the panel's check is the signature's."""
    header = bytearray(24)
    header[0], header[1], header[23] = 0xE9, 1, 1
    struct.pack_into("<H", header, 12, chip)
    desc = bytearray(256)
    struct.pack_into("<I", desc, 0, 0xABCD5432)
    desc[16:16 + len(version)] = version.encode()
    desc[48:48 + len(project)] = project.encode()
    segment = bytes(desc) + b"\x5a" * filler
    body = bytes(header) + struct.pack("<II", 0x3C000020, len(segment)) + segment
    checked = (len(body) + 1 + 15) // 16 * 16
    body += b"\x00" * (checked - len(body))
    body += hashlib.sha256(body).digest()
    body += b"\x00" * (-len(body) % 4096)
    if not signed:
        return body
    block = bytearray(4096)
    block[:4] = b"\xe7\x02\x00\x00"
    block[4:36] = hashlib.sha256(body).digest()
    struct.pack_into("<I", block, 1196, zlib.crc32(bytes(block[:1196])) & 0xFFFFFFFF)
    return body + bytes(block)


def refused(label, data, fragment):
    try:
        panelfw.inspect(data)
    except panelfw.ImageRefused as exc:
        check(label, fragment in str(exc), str(exc))
    else:
        check(label, False, "accepted")


good = make_image("0.2.0")
eq("a good image reads as its version", panelfw.inspect(good)[0], "0.2.0")
refused("an empty upload is refused", b"", "empty")
refused("a text file is refused", b"hello" * 100, "not panel firmware")
refused("an image for another board is refused", make_image(project="vxp-io-8di8ro"), "another board")
refused("an image for another chip is refused", make_image(chip=0x0005), "another chip")
refused("an unsigned image is refused", make_image(signed=False), "not signed")
refused("an image with no readable version is refused", make_image(version="v2"), "version")
refused("a truncated image is refused", good[:len(good) - 4096 - 100], "not signed")
flipped = bytearray(good)
flipped[200] ^= 0xFF
refused("a flipped byte is refused on the image's own checksum", bytes(flipped), "damaged")
resigned = bytearray(good)
resigned[-4096 + 10] ^= 0xFF
refused("a signature that is not for these bytes is refused", bytes(resigned), "not for these bytes")
refused("an image over the slot is refused", good + b"\x00" * panelfw.SLOT_BYTES, "larger")
merged = bytearray(good)
struct.pack_into("<I", merged, 32, 0)
refused("the merged serial image (no application descriptor) is refused", bytes(merged), "serial-flash")
check("0.1.0 -> 0.2.0 is forward", panelfw.newer("0.2.0", "0.1.0"))
check("0.10.0 is newer than 0.9.0, as numbers", panelfw.newer("0.10.0", "0.9.0"))
check("the same version is not forward", not panelfw.newer("0.2.0", "0.2.0"))
check("an older one is not", not panelfw.newer("0.1.0", "0.2.0"))
check("a version nobody can read is not", not panelfw.newer("0.2.0", "") and not panelfw.newer("x", "0.1.0"))

real = Path(__file__).resolve().parent.parent / "firmware" / "relay-display" / "build-qemu" / "relay-display-app.bin"
if real.exists():
    check("a real built and signed image passes the same check", bool(panelfw.inspect(real.read_bytes())[0]))

print("\nfirmware: upload, store and serve")
eq("no firmware at first", admin.get("/api/admin/panels/firmware").json()["firmware"], None)
eq("only the CrowPanel is listed: the Waveshare is on hold",
   [b["board"] for b in admin.get("/api/admin/panels/firmware").json()["boards"]], ["crowpanel-adv7"])
eq("the viewer port serves nothing before an upload",
   viewer.get("/api/panels/firmware/crowpanel-adv7/0.2.0.bin").status_code, 404)
r = admin.post("/api/admin/panels/firmware", content=make_image(signed=False))
eq("an unsigned upload is refused with the reason", (r.status_code, "not signed" in r.text), (400, True))
check("…and nothing is kept", panelfw.current() is None)
r = admin.post("/api/admin/panels/firmware?filename=relay-display-app.bin", content=good,
               headers={"content-type": "application/octet-stream"})
eq("a good image is kept", r.status_code, 200)
info = r.json()["firmware"]
eq("it reports its version, size and checksum",
   (info["version"], info["size"], info["sha256"]), ("0.2.0", len(good), hashlib.sha256(good).hexdigest()))
eq("the panels frame carries it", main._panels_frame()["firmware"]["version"], "0.2.0")
eq("it is the CrowPanel's", info["board"], "crowpanel-adv7")
r = viewer.get("/api/panels/firmware/crowpanel-adv7/0.2.0.bin")
eq("a panel gets exactly the bytes, on the viewer port", (r.status_code, r.content == good), (200, True))
eq("…with a length", r.headers.get("content-length"), str(len(good)))
eq("a version that is not the one held is 404",
   viewer.get("/api/panels/firmware/crowpanel-adv7/0.3.0.bin").status_code, 404)
eq("a name that is not a version is 404",
   viewer.get("/api/panels/firmware/crowpanel-adv7/..%2Fconfig.json").status_code, 404)
eq("…and so is one without the suffix", viewer.get("/api/panels/firmware/crowpanel-adv7/0.2.0").status_code, 404)
eq("…and a board Relay does not know", viewer.get("/api/panels/firmware/nope/0.2.0.bin").status_code, 404)
eq("…and the Waveshare's, which is on hold",
   viewer.get("/api/panels/firmware/waveshare-p4-10.1/0.2.0.bin").status_code, 404)
r = admin.post("/api/admin/panels/firmware", content=make_image("0.9.0", chip=panelfw.CHIP_ESP32_P4))
eq("a Waveshare (ESP32-P4) image is refused: that board is on hold",
   (r.status_code, "on hold" in r.text), (400, True))
eq("…and naming that board is refused too",
   admin.post("/api/admin/panels/firmware?board=waveshare-p4-10.1", content=good).status_code, 400)
eq("naming the CrowPanel for a CrowPanel image is fine",
   admin.post("/api/admin/panels/firmware?board=crowpanel-adv7", content=good).status_code, 200)
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
eq("the upload route is not on the viewer port",
   viewer.post("/api/admin/panels/firmware", content=good).status_code, 404)
eq("the upload needs the admin token",
   TestClient(app, base_url="http://relay.local:8001").post("/api/admin/panels/firmware", content=good).status_code, 401)
big = admin.post("/api/admin/panels/firmware", content=b"\x00" * (panelfw.SLOT_BYTES + 1))
eq("an upload over the slot is cut off at the door", big.status_code, 413)
check("the image survives a restart: it is read from disk", panelfw.current() is not None and panelfw.current().version == "0.2.0")

print("\nfirmware: asking panels to update")
clock[0] += 1000
for n in (30, 31, 32):
    viewer.post("/api/panels/checkin", json=body(n, version="0.1.0"))
viewer.post("/api/panels/checkin", json=body(33, version="0.2.0"))
viewer.post("/api/panels/checkin", json=body(34, version="0.1.0"))
rows = {p["id"]: p for p in main._panels_frame()["panels"]}
eq("a panel on an older version shows an update available", rows[pid(30)]["update_available"], True)
eq("a panel already on it does not", rows[pid(33)]["update_available"], False)
eq("an ask that does nothing is refused", admin.post("/api/admin/panels/firmware/update", json={"id": pid(33)}).status_code, 400)
eq("an unknown panel is 404", admin.post("/api/admin/panels/firmware/update", json={"id": pid(77)}).status_code, 404)
r = viewer.post("/api/panels/checkin", json=body(30, version="0.1.0"))
check("a panel that was not asked is not offered anything", "update" not in r.json())

q = reg.subscribe(pid(30))
seen_frames = []
reg.on_change = lambda: seen_frames.append(1)
n = 0
r = admin.post("/api/admin/panels/firmware/update", json={"id": pid(30)})
eq("the operator asks one panel", r.status_code, 200)
check("…which tells the admin stream", len(seen_frames) > n)
eq("…and the row says it was asked", (r.json()["panel"]["update_requested"], r.json()["panel"]["update"]["state"]),
   (True, "queued"))
frame = q.get_nowait()
msg = json.loads(frame.decode().split("data: ", 1)[1])
eq("the stream gets update_available with what to fetch",
   msg, {"type": "update_available", "version": "0.2.0", "size": len(good),
         "sha256": hashlib.sha256(good).hexdigest(), "path": "/api/panels/firmware/crowpanel-adv7/0.2.0.bin", "now": False})
reg.unsubscribe(pid(30), q)
r = viewer.post("/api/panels/checkin", json=body(30, version="0.1.0"))
eq("the check-in reply carries the same offer", r.json()["update"]["path"], "/api/panels/firmware/crowpanel-adv7/0.2.0.bin")
q = reg.subscribe(pid(30))
check("a stream opened later is told at once", json.loads(q.get_nowait().decode().split("data: ", 1)[1])["type"] == "update_available")
reg.unsubscribe(pid(30), q)
check("a panel that was not asked gets nothing on its stream",
      reg.subscribe(pid(31)).empty())

r = admin.post("/api/admin/panels/firmware/update", json={"id": pid(31), "now": True})
eq("`now` is carried", viewer.post("/api/panels/checkin", json=body(31, version="0.1.0")).json()["update"]["now"], True)
n_online = len([p for p in reg.panels.values() if reg.online(p) and reg.update_available(p)])
clock[0] += panels.OFFLINE_AFTER_S + 1
viewer.post("/api/panels/checkin", json=body(32, version="0.1.0"))   # 32 comes back; 34 stays quiet
r = admin.post("/api/admin/panels/firmware/update", json={})
eq("`all` asks the panels that are online and leaves the offline ones",
   (r.json()["requested"], r.json()["offline"] >= 1), (1, True))
check("…and the offline one was not asked", not reg.panels[pid(34)].update_request)

print("\nfirmware: what a panel reports")
for state, extra in (("downloading", {"progress": 40}), ("ready", {}), ("restarting", {})):
    r = viewer.post(f"/api/panels/{pid(30)}/update", json={"state": state, "version": "0.2.0", **extra})
    eq(f"a panel reports {state}", r.status_code, 200)
u = reg.view(pid(30))["update"]
eq("the row carries its latest word", (u["state"], u["progress"], u["error"]), ("restarting", 100, ""))
viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "downloading", "version": "0.2.0", "progress": 250})
eq("progress is clamped", reg.view(pid(30))["update"]["progress"], 100)
viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "downloading", "version": "0.2.0", "progress": 40})
eq("…and passed on", reg.view(pid(30))["update"]["progress"], 40)
check("a report does not end the ask while it is going", reg.panels[pid(30)].update_request is not None)
viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "failed", "version": "0.2.0", "error": "signature"})
u = reg.view(pid(30))["update"]
eq("a refusal on the panel is shown with its reason", (u["state"], u["error"], "signed" in u["error_text"]),
   ("failed", "signature", True))
check("…and ends the ask, so a refused image is not offered in a loop", reg.panels[pid(30)].update_request is None)
check("…and the next check-in carries no offer",
      "update" not in viewer.post("/api/panels/checkin", json=body(30, version="0.1.0")).json())
viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "failed", "error": "<script>"})
eq("an error word Relay does not know is dropped", reg.view(pid(30))["update"]["error"], "")
viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "melting"})
eq("a state Relay does not know changes nothing", reg.view(pid(30))["update"]["state"], "failed")
eq("a report from an unknown panel is 404", viewer.post(f"/api/panels/{pid(77)}/update", json={"state": "ready"}).status_code, 404)
eq("a report that is not JSON is 400",
   viewer.post(f"/api/panels/{pid(30)}/update", content=b"{", headers={"content-type": "application/json"}).status_code, 400)
eq("an oversized report is 413",
   viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "failed", "error": "x" * 2000}).status_code, 413)
viewer.post(f"/api/panels/{pid(30)}/update", json={"state": "rolled_back", "version": "0.2.0"})
u = reg.view(pid(30))["update"]
eq("a rollback is reported as one, with its reason", (u["state"], u["error"]), ("rolled_back", "rollback"))

admin.post("/api/admin/panels/firmware/update", json={"id": pid(30)})
viewer.post("/api/panels/checkin", json=body(30, version="0.2.0"))
u = reg.view(pid(30))["update"]
eq("a check-in from the new version is the result, whether or not the panel said so",
   (u["state"], u["version"]), ("installed", "0.2.0"))
check("…and ends the ask", reg.panels[pid(30)].update_request is None)
eq("…and the update is no longer available", reg.view(pid(30))["update_available"], False)

print("\nfirmware: replaced or removed while panels are asked")
admin.post("/api/admin/panels/firmware/update", json={"id": pid(31), "now": False})
admin.post("/api/admin/panels/firmware", content=make_image("0.3.0"))
check("a new image withdraws the asks for the old one", reg.panels[pid(31)].update_request is None)
check("…so the panel's next check-in carries no offer, and it drops what it had staged",
      "update" not in viewer.post("/api/panels/checkin", json=body(31, version="0.1.0")).json())
eq("…and its row no longer says it is going", reg.view(pid(31))["update"], None)
eq("the old version is no longer served", viewer.get("/api/panels/firmware/crowpanel-adv7/0.2.0.bin").status_code, 404)
eq("the new one is", viewer.get("/api/panels/firmware/crowpanel-adv7/0.3.0.bin").status_code, 200)
r = admin.delete("/api/admin/panels/firmware")
eq("the operator removes the firmware", (r.status_code, r.json()["removed"]), (200, True))
eq("…and nothing is held", admin.get("/api/admin/panels/firmware").json()["firmware"], None)
eq("…or served", viewer.get("/api/panels/firmware/crowpanel-adv7/0.3.0.bin").status_code, 404)
eq("asking for an update with none held is 409",
   admin.post("/api/admin/panels/firmware/update", json={"id": pid(31)}).status_code, 409)
eq("…and so is asking for all", admin.post("/api/admin/panels/firmware/update", json={}).status_code, 409)
check("a panel row has no update available", not reg.view(pid(31))["update_available"])


print("\nfirmware: one image per board, and the store from before #122")
viewer.post("/api/panels/checkin", json=body(40, version="0.1.0", board="crowpanel-adv7"))
viewer.post("/api/panels/checkin", json=body(41, version="0.1.0", board="waveshare-p4-10.1"))
viewer.post("/api/panels/checkin", json=body(42, version="0.1.0"))
admin.post("/api/admin/panels/firmware", content=make_image("0.4.0"))
eq("a panel that reports the CrowPanel is offered its image", reg.view(pid(40))["update_available"], True)
eq("…and so is one that reports no board: it is a CrowPanel", reg.view(pid(42))["update_available"], True)
check("…which is how every panel from before #122 checks in", reg.view(pid(42))["board"] == "crowpanel-adv7")
eq("a panel of a board on hold is offered nothing", reg.view(pid(41))["update_available"], False)
r = admin.post("/api/admin/panels/firmware/update", json={"id": pid(41)})
eq("…and asking it is refused with why", (r.status_code, "waveshare-p4-10.1" in r.text), (409, True))
eq("the list says each panel's board", (reg.view(pid(40))["board_label"], reg.view(pid(41))["board"]),
   ('CrowPanel Advance 7"', "waveshare-p4-10.1"))
fw = {b["board"]: b for b in main._panels_frame()["boards"]}
check("the firmware row counts the CrowPanels online, and those behind",
      fw["crowpanel-adv7"]["online"] >= 2 and fw["crowpanel-adv7"]["outdated"] >= 2, fw)
admin.delete("/api/admin/panels/firmware")
# A store written before #122: one image at panel-firmware/panel.bin.
legacy = make_image("0.5.0")
panelfw.directory().mkdir(parents=True, exist_ok=True)
(panelfw.directory() / "panel.bin").write_bytes(legacy)
(panelfw.directory() / "panel.json").write_text(json.dumps(
    {"version": "0.5.0", "size": len(legacy), "sha256": hashlib.sha256(legacy).hexdigest(),
     "uploaded_at": time.time(), "filename": "relay-display-app.bin"}))
eq("an image kept before #122 is the CrowPanel's", panelfw.current("crowpanel-adv7").version, "0.5.0")
check("…moved into its board's folder", (panelfw.directory() / "crowpanel-adv7" / "panel.bin").exists()
      and not (panelfw.directory() / "panel.bin").exists())
eq("…and served there", viewer.get("/api/panels/firmware/crowpanel-adv7/0.5.0.bin").status_code, 200)
admin.delete("/api/admin/panels/firmware")

print("\nboard, rotation, the Service feed and a restart (#122)")
clock[0] += 1000
r = viewer.post("/api/panels/checkin", json=body(50, board="crowpanel-adv7", rotation=0))
check("a panel with no rotation set is told none", "set_rotation" not in r.json())
eq("the list shows the rotation it reports", reg.view(pid(50))["rotation"], 0)
viewer.post("/api/panels/checkin", json=body(51, board="x" * 40, rotation=45))
eq("a board or rotation that is not one is dropped", (reg.panels[pid(51)].board, reg.view(pid(51))["rotation"]), ("", None))
r = admin.post(f"/api/admin/panels/{pid(50)}", json={"rotation": 180})
eq("the operator turns a panel upside down", (r.status_code, r.json()["set_rotation"]), (200, 180))
eq("…which is saved with a revision", (config.get()["panels"][pid(50)]["rotation"],
                                       config.get()["panels"][pid(50)]["rotation_rev"]), (180, 1))
eq("…and is in the next check-in reply until the panel applies it",
   viewer.post("/api/panels/checkin", json=body(50, rotation=0)).json()["set_rotation"],
   {"type": "set_rotation", "rev": 1, "deg": 180})
r = viewer.post("/api/panels/checkin", json=body(50, rotation=180, rotation_rev=1))
check("a panel that applied it is not told again", "set_rotation" not in r.json())
eq("…and the change is the operator's", reg.view(pid(50))["rotation_changed_by"], "admin")
r = viewer.post("/api/panels/checkin", json=body(50, rotation=0, rotation_rev=1))
check("a turn at the screen afterwards is not undone", "set_rotation" not in r.json())
eq("…and is shown as made at the screen", (reg.view(pid(50))["rotation"], reg.view(pid(50))["rotation_changed_by"]),
   (0, "screen"))
eq("a rotation that is not 0, 90, 180 or 270 is refused", admin.post(f"/api/admin/panels/{pid(50)}", json={"rotation": 45}).status_code, 400)
eq("…and so is true", admin.post(f"/api/admin/panels/{pid(50)}", json={"rotation": True}).status_code, 400)
q = reg.subscribe(pid(50))
check("a stream that opens is told nothing the panel has applied", q.empty())
admin.post(f"/api/admin/panels/{pid(50)}", json={"rotation": 180})
eq("setting it again is a new revision, pushed on the stream", json.loads(q.get_nowait().decode()[len("data: "):]),
   {"type": "set_rotation", "rev": 2, "deg": 180})
reg.unsubscribe(pid(50), q)
q = reg.subscribe(pid(50))
frames = []
while not q.empty():
    frames.append(json.loads(q.get_nowait().decode()[len("data: "):]))
check("a stream that opens is told a revision not yet applied", {"type": "set_rotation", "rev": 2, "deg": 180} in frames, frames)
r = admin.post(f"/api/admin/panels/{pid(50)}/reboot")
eq("Reboot reaches a panel with its stream open", (r.status_code, json.loads(q.get_nowait().decode()[6:])),
   (200, {"type": "reboot"}))
reg.unsubscribe(pid(50), q)
r = admin.post(f"/api/admin/panels/{pid(50)}/reboot")
eq("…and is refused for one with none: a restart is never queued", (r.status_code, "not connected" in r.text), (409, True))
check("…nor carried in a check-in reply", "reboot" not in json.dumps(viewer.post("/api/panels/checkin", json=body(50)).json()))
eq("Reboot for an unknown panel is 404", admin.post(f"/api/admin/panels/{pid(99)}/reboot").status_code, 404)
eq("Reboot is not on the viewer port", viewer.post(f"/api/admin/panels/{pid(50)}/reboot").status_code, 404)

config.save({"planning_center": {"service_types": ["7", "9"]}})
svc = [{"stream": "service", "service_type": "7"}]
r = admin.post(f"/api/admin/panels/{pid(50)}", json={"feeds": svc})
eq("a Service feed needs the panel to show the service", (r.status_code, "service mode" in r.text), (400, True))
r = admin.post(f"/api/admin/panels/{pid(50)}", json={"feeds": svc, "service_mode": "show"})
eq("…and is set with it", (r.status_code, r.json()["set_feeds"]), (200, svc))
eq("a type Relay does not follow is refused",
   admin.post(f"/api/admin/panels/{pid(50)}", json={"feeds": [{"stream": "service", "service_type": "8"}]}).status_code, 400)
r = admin.post(f"/api/admin/panels/{pid(50)}", json={"feeds": svc + [{"stream": "transcription"}]})
eq("the service beside the transcription is two feeds", (r.status_code, len(r.json()["set_feeds"])), (200, 2))
r = admin.post(f"/api/admin/panels/{pid(50)}", json={"service_type": "9"})
eq("holding the panel to another type while it shows this one is refused", r.status_code, 400)
r = admin.post(f"/api/admin/panels/{pid(50)}", json={"service_mode": "control"})
eq("…and so is a mode that does not show the service", r.status_code, 400)
reply = viewer.post("/api/panels/checkin", json=body(50, feed_rev=0)).json()
eq("the panel is sent the Service feed", reply["set_feed"]["feeds"], svc + [{"stream": "transcription"}])
viewer.post("/api/panels/checkin", json=body(50, feeds=svc, feed_rev=reply["set_feed"]["rev"]))
eq("a panel reports a Service feed it shows", reg.view(pid(50))["feeds"], svc)
eq("clean_feeds refuses a service with no type", panels.clean_feeds([{"stream": "service"}]), None)
eq("…or a type the panel could not hold", panels.clean_feeds([{"stream": "service", "service_type": "1" * 20}]), None)

print("\nfirmware: the panel's words and Relay's are the same")
fw_source = (Path(__file__).resolve().parent.parent / "firmware" / "relay-display" / "main" / "update_format.h")
check("the panel's update vocabulary is where this test reads it", fw_source.exists())
if fw_source.exists():
    import re as _re
    text = fw_source.read_text()

    def words(fn):
        body = text.split(f"inline const char *{fn}(")[1].split("\ninline ")[0]
        return set(_re.findall(r'return "([a-z_]+)";', body)) - {""}

    sent_states, sent_errors = words("state_word"), words("error_word")
    # `installed` and `rolled_back` are the outcomes `firmware_update.cpp` reports after a boot, not states.
    check("every state the panel sends is one Relay keeps",
          sent_states | {"installed", "rolled_back"} <= set(panels.UPDATE_STATES), sent_states)
    check("…and Relay keeps no state the panel never sends",
          set(panels.UPDATE_STATES) <= sent_states | {"installed", "rolled_back"})
    eq("the errors are the same words, both ways", sent_errors, set(panels.UPDATE_ERRORS))

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
