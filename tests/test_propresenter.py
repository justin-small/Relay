"""ProPresenter (#138): the follower, the lyrics feed, view-only mode, the stage message, Alerts, the admin routes.

Offline: a fake ProPresenter (a real HTTP server on 127.0.0.1) answers the way ProPresenter 22 did when it was
tried live (docs/reference/propresenter-live-test.md): `POST /v1/status/updates` streams `{"url","data"}` messages
separated by a blank line, in HTTP chunks that cut messages anywhere; slide pushes are sometimes missed;
`timer/system_time` arrives unasked; one unknown path fails the request with 400.

Run: python tests/test_propresenter.py
"""
import asyncio
import json
import logging
import os
import queue
import socket
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from fastapi.testclient import TestClient

from app import config, main as app_main
from app.modules.propresenter import propresenter as pp
from app.alerts import alerts
from app.main import app

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label + (f" ({extra})" if extra and not cond else ""), bool(cond), True)


# The 46 paths a live install accepted for the stream (docs/reference/propresenter-live-test.md).
ACCEPTED = {
    "announcement/active", "announcement/slide_index", "announcement/active/timeline", "audio/playlists",
    "audio/playlist/active", "audio/playlist/focused", "capture/status", "looks", "look/current", "macros",
    "macro_collections", "media/playlists", "media/playlist/active", "media/playlist/focused", "messages",
    "playlists", "playlist/active", "playlist/focused", "presentation/active", "presentation/current",
    "presentation/focused", "presentation/slide_index", "presentation/active/timeline",
    "presentation/focused/timeline", "presentation/chord_chart/updates", "props", "prop_collections",
    "stage/message", "stage/layout_map", "stage/screens", "stage/layouts", "status/layers",
    "status/stage_screens", "status/audience_screens", "status/screens", "status/slide", "timers",
    "timers/current", "timer/system_time", "timer/video_countdown", "transport/presentation/current",
    "transport/presentation/time", "transport/audio/time"}


def slide(text):
    return {"text": text, "notes": "", "uuid": "S-" + text[:4]}


class Fake:
    """ProPresenter, as far as Relay uses it. Mutate the attributes from the test; `push()` sends a stream
    message (or doesn't, to model a missed push)."""

    def __init__(self):
        self.version = {"name": "Studio-Mac", "platform": "mac", "os_version": "26.5.2",
                        "host_description": "ProPresenter 22", "api_version": "v1"}
        self.presentation = None          # {"uuid", "name", "path", "groups": [(name, [text...])]}
        self.index = None
        self.stage = ""
        self.beat = True                  # send timer/system_time
        self.refuse = False               # answer the stream request with 503
        self.requests = []                # (method, path, body) of everything except the stream
        self.thumb_calls = 0
        self.msg_active = False
        self.timer_rows = []              # what GET /v1/timers/current answers: [{"id": {...}, "time", "state"}]
        self.timer_cfg = None             # what GET /v1/timers answers, when a test sets it
        self.timer_cfg_calls = 0
        self.announcement = None          # {"uuid", "name", "groups": [(name, [text...])]}
        self.ann_index = None
        self.screens = {"audience": False, "stage": False}
        self.missing = set()              # presentation UUIDs that answer 404
        self.notes = {}                   # slide number (0-based, in listed order) -> notes text
        self.stream_count = 0
        self.stream_bodies = []
        self.out = queue.Queue()
        self.kill = threading.Event()
        self.lock = threading.Lock()

    # -- the state as GET answers it -------------------------------------------------------------------
    def texts(self):
        if not self.presentation:
            return []
        return [t for _, g in self.presentation["groups"] for t in g]

    def status_slide(self):
        t = self.texts()
        if self.presentation is None or self.index is None or self.index >= len(t):
            return getattr(self, "stale", None)
        nxt = slide(t[self.index + 1]) if self.index + 1 < len(t) else None
        cur = slide(t[self.index])
        cur["notes"] = self.notes.get(self.index, "")
        if nxt:
            nxt["notes"] = self.notes.get(self.index + 1, "")
        return {"current": cur, "next": nxt}

    def slide_no(self, gi, si):
        """The listed number of slide `si` of group `gi`."""
        return sum(len(g) for _, g in self.presentation["groups"][:gi]) + si

    def current_body(self):
        p = self.presentation
        return {"presentation": {"id": {"uuid": p["uuid"], "name": p["name"], "index": 0}} if p else None}

    def index_body(self):
        p = self.presentation
        return {"presentation_index": ({"index": self.index, "presentation_id": {"uuid": p["uuid"], "name": p["name"],
                                                                                 "index": 0}}
                                       if p and self.index is not None else None)}

    def detail(self, uuid):
        p = self.presentation
        d = {
            "id": {"uuid": p["uuid"], "name": p["name"], "index": 0}, "presentation_path": p["path"],
            "groups": [{"name": n, "slides": [{"text": t, "notes": self.notes.get(self.slide_no(gi, si), "")}
                                              for si, t in enumerate(g)], "uuid": "G-" + n,
                        "color": {"red": 1.0, "green": 0.0, "blue": 0.5, "alpha": 1}}
                       for gi, (n, g) in enumerate(p["groups"])]}
        if p.get("arrangement"):   # the order the groups are played in; a name may repeat
            d["arrangements"] = [{"id": {"uuid": "ARR", "name": "Master", "index": 0},
                                  "groups": ["G-" + n for n in p["arrangement"]]}]
            d["current_arrangement"] = "ARR"
        return {"presentation": d}

    # -- the control set (stage 2) -----------------------------------------------------------------------
    def extra_get(self, path):
        """(status, body, content type) for the control paths, or None."""
        base = path.split("?")[0]
        j = lambda o: (200, (json.dumps(o) + "\r\n\r\n").encode(), "application/json")
        if base == "/v1/announcement/active":
            a = self.announcement
            return j({"announcement": None if not a else {
                "id": {"uuid": a["uuid"], "name": a["name"], "index": 0},
                "groups": [{"name": n, "color": {"red": 0, "green": 1, "blue": 0, "alpha": 1},
                            "slides": [{"text": t, "notes": "ann note " + t, "label": "", "enabled": True} for t in g]}
                           for n, g in a["groups"]]}})
        if base == "/v1/announcement/slide_index":
            a = self.announcement
            return j({"announcement_index": None if not a or self.ann_index is None else {
                "index": self.ann_index, "presentation_id": {"uuid": a["uuid"], "name": a["name"], "index": 0}}})
        if base == "/v1/status/screens":
            return j([{"id": {"uuid": "SCR", "name": "Screen", "index": 0}, "size": {"width": 1920, "height": 1080},
                       "screen_type": "audience"}])
        if base in ("/v1/status/audience_screens", "/v1/status/stage_screens"):
            return j(self.screens[base.split("/")[-1].split("_")[0]])
        if base == "/v1/playlist/PL3":
            return j({"id": {"uuid": "PL3", "name": "Setlist", "index": 2}, "items": [
                {"id": {"uuid": "H0", "name": "Worship", "index": 0}, "type": "header", "is_hidden": False,
                 "is_pco": False, "header_color": {"red": 0, "green": 0, "blue": 1, "alpha": 1}},
                {"id": {"uuid": "S1", "name": "Great Song", "index": 1}, "type": "presentation", "is_hidden": False,
                 "is_pco": True, "presentation_info": {"presentation_uuid": "P-Grea", "arrangement_name": "Master",
                                                       "arrangement_uuid": "ARR"}},
                {"id": {"uuid": "S2", "name": "Gone Song", "index": 2}, "type": "presentation", "is_hidden": True,
                 "is_pco": False, "presentation_info": {"presentation_uuid": "P-GONE", "arrangement_name": "",
                                                        "arrangement_uuid": ""}},
                {"id": {"uuid": "S3", "name": "Empty slot", "index": 3}, "type": "placeholder", "is_hidden": False,
                 "is_pco": False},
                {"id": {"uuid": "S4", "name": "Unlinked", "index": 4}, "type": "presentation", "is_hidden": False,
                 "is_pco": False, "presentation_info": {"presentation_uuid": ""}},
                {"id": {"uuid": "H1", "name": "Sermon", "index": 5}, "type": "header", "is_hidden": False,
                 "is_pco": False, "header_color": {"red": 1, "green": 0, "blue": 0, "alpha": 1}}]})
        if base == "/v1/playlists":
            return j([{"id": {"uuid": "PL1", "name": "Sunday", "index": 0}, "field_type": "playlist", "children": []},
                      {"id": {"uuid": "GRP", "name": "Folder", "index": 1}, "field_type": "group",
                       "children": [{"id": {"uuid": "PL2", "name": "Youth", "index": 0}, "field_type": "playlist",
                                     "children": []}]}])
        if base == "/v1/playlist/PL1":
            return j({"id": {"uuid": "PL1", "name": "Sunday", "index": 0}, "items": [
                {"id": {"uuid": "I0", "name": "Welcome", "index": 0}, "type": "header", "is_hidden": False,
                 "header_color": {"red": 1, "green": 0, "blue": 0, "alpha": 1}},
                {"id": {"uuid": "I1", "name": "Great Song", "index": 1}, "type": "presentation", "is_hidden": False,
                 "presentation_info": {"presentation_uuid": "P-Grea"}}]})
        if base == "/v1/macros":
            return j([{"id": {"uuid": "M1", "name": "Lights up", "index": 0}, "color": {"red": 0, "green": 1, "blue": 0}},
                      {"id": {"uuid": "M2", "name": "Reboot", "index": 1}, "color": {"red": 1, "green": 0, "blue": 0}}])
        if base == "/v1/looks":
            return j([{"id": {"uuid": "L1", "name": "Default", "index": 0}, "screens": []},
                      {"id": {"uuid": "L2", "name": "Sermon", "index": 1}, "screens": []}])
        if base == "/v1/timers/current":
            return j(self.timer_rows)
        if base == "/v1/timers":
            self.timer_cfg_calls += 1
            if self.timer_cfg is not None:
                return j(self.timer_cfg)
            return j([{"id": {"uuid": "T1", "name": "Segment", "index": 0}, "countdown": {"duration": 300}}])
        if base == "/v1/messages":
            return j([{"id": {"uuid": "MSG1", "name": "Nursery", "index": 0}, "message": "Room {room}",
                       "tokens": [{"name": "room", "text": {"text": "3"}}], "is_active": self.msg_active}])
        if "/thumbnail/" in base:
            self.thumb_calls += 1
            return (200, b"\xff\xd8JPEG" + base.encode(), "image/jpeg")
        if base.startswith("/v1/playlist/focused/") and getattr(self, "no_focus", False):
            return (404, b"", "application/json")
        if base.endswith("/trigger") or "/clear" in base or base.startswith("/v1/timer/") or base.startswith("/v1/timers/"):
            if base.startswith("/v1/message/") and base.endswith("/clear"):
                self.msg_active = False
            return (204, b"", "application/json")
        return None

    def extra_write(self, method, path, raw):
        if method == "POST" and path == "/v1/message/MSG1/trigger":
            self.msg_active = True
            return (204, b"", "application/json")
        if method == "PUT" and path == "/v1/transport/presentation/time":
            return (204, b"", "application/json")
        if method == "PUT" and path in ("/v1/status/audience_screens", "/v1/status/stage_screens"):
            value = json.loads(raw)
            if not isinstance(value, bool):
                return (400, b"bad body", "text/plain")
            self.screens[path.split("/")[-1].split("_")[0]] = value
            return (204, b"", "application/json")
        return None

    def body(self, path):
        return json.loads(self.extra_get(path)[1].decode())

    def stream_message(self, url):
        data = {"status/slide": self.status_slide(), "presentation/current": self.current_body(),
                "presentation/slide_index": self.index_body(), "stage/message": self.stage,
                "timers/current": self.timer_rows, "status/layers": {"slide": bool(self.presentation)},
                "playlist/active": {}, "look/current": {"id": {"name": "Default"}},
                "announcement/active": self.body("/v1/announcement/active"),
                "announcement/slide_index": self.body("/v1/announcement/slide_index"),
                "status/screens": self.body("/v1/status/screens"),
                "status/audience_screens": self.screens["audience"],
                "status/stage_screens": self.screens["stage"]}[url]
        return json.dumps({"url": url, "data": data}).encode() + b"\r\n\r\n"

    def push(self, *urls):
        for u in urls:
            self.out.put(self.stream_message(u))

    def song(self, name="Great Song  ", library="Songs", groups=None):
        self.presentation = {"uuid": "P-" + name.strip()[:4], "name": name,
                             "path": f"/Users/x/Libraries/{library}/{name}.pro",
                             "groups": groups or [("Verse 1", ["line one\nline two", "line three"]),
                                                  ("Chorus", ["chorus a"])]}
        self.index = 0

    def clear(self):
        self.stale = self.status_slide()   # status/slide keeps its old text after a clear
        self.presentation, self.index = None, None


def make_handler(fake):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _reply(self, code, body=b"", ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj):
            self._reply(200, (json.dumps(obj) + "\r\n\r\n").encode() if obj is not None else b"")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            with fake.lock:
                fake.requests.append(("GET", self.path, None))
            p = self.path
            x = fake.extra_get(p) if p != "/v1/trigger/next" and p != "/v1/trigger/previous" else None
            if x is not None and not (p.startswith("/v1/presentation/") and "/thumbnail/" not in p and "/trigger" not in p):
                return self._reply(*x)
            if p == "/version":
                return self._json(fake.version)
            if p == "/v1/status/slide":
                return self._json(fake.status_slide())
            if p == "/v1/presentation/current":
                return self._json(fake.current_body())
            if p == "/v1/presentation/slide_index":
                return self._json(fake.index_body())
            if p.startswith("/v1/presentation/") and p.rsplit("/", 1)[1] in fake.missing:
                return self._reply(404)
            if p.startswith("/v1/presentation/") and fake.presentation:
                return self._json(fake.detail(p.rsplit("/", 1)[1]))
            if p == "/v1/stage/message":
                return self._json(fake.stage)
            if p.startswith("/v1/trigger/") or p.startswith("/v1/clear/"):
                return self._reply(204)
            self._reply(404)

        def do_PUT(self):
            raw = self._body()
            with fake.lock:
                fake.requests.append(("PUT", self.path, raw))
            x = fake.extra_write("PUT", self.path, raw)
            if x is not None:
                return self._reply(*x)
            if self.path == "/v1/stage/message":
                try:
                    value = json.loads(raw)
                except ValueError:
                    return self._reply(400, b"bad json")
                if not isinstance(value, str):
                    return self._reply(400, b"Json deserialize error: invalid type: map, expected a string")
                fake.stage = value
                fake.push("stage/message")
                return self._reply(204)
            self._reply(404)

        def do_DELETE(self):
            with fake.lock:
                fake.requests.append(("DELETE", self.path, None))
            if self.path == "/v1/stage/message":
                fake.stage = ""
                fake.push("stage/message")
                return self._reply(204)
            self._reply(404)

        def do_POST(self):
            raw = self._body()
            if self.path != "/v1/status/updates":
                with fake.lock:
                    fake.requests.append(("POST", self.path, raw))
                x = fake.extra_write("POST", self.path, raw)
                return self._reply(*x) if x is not None else self._reply(404)
            paths = json.loads(raw)
            fake.stream_bodies.append(paths)
            fake.stream_count += 1
            if fake.refuse:
                return self._reply(503, b"busy")
            bad = [p for p in paths if p not in ACCEPTED]
            if bad:
                return self._reply(400, json.dumps([f"URL: {bad[0]}. Error: 404 Not Found"]).encode())
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def chunk(b):
                self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
                self.wfile.flush()

            try:
                # The current state of every requested path arrives at once, as one message cut across two
                # HTTP chunks and two messages sharing a chunk.
                first = b"".join(fake.stream_message(u) for u in paths)
                cut = len(first) // 3
                chunk(first[:cut])
                chunk(first[cut:])
                last_beat = 0.0
                while not fake.kill.is_set():
                    try:
                        data = fake.out.get(timeout=0.05)
                        mid = len(data) // 2
                        chunk(data[:mid])
                        time.sleep(0.01)
                        chunk(data[mid:])
                    except queue.Empty:
                        pass
                    if fake.beat and time.monotonic() - last_beat > 0.2:
                        last_beat = time.monotonic()
                        chunk(json.dumps({"url": "timer/system_time", "data": int(time.time())}).encode()
                              + b"\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return H


class Server:
    def __init__(self):
        self.fake = Fake()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.fake.kill.set()
        self.httpd.shutdown()


# Quick timings so the stall and backoff paths run in seconds, not minutes.
pp.STALL_S = 1.5
pp.RESYNC_S = 0.2
pp.BACKOFF_S = (0.1, 0.2)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"propresenter": {"enabled": True}, "planning_center": {"enabled": True}}})
# Both modules are switched on (#157); their hooks into core are set without starting their background work.
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")
_modules.attach("propresenter")
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = _db.client(app, base_url="http://relay.local:8001")


def configure(srv, **extra):
    s = {"enabled": True, "host": "127.0.0.1", "port": srv.port, "songs_library": "Songs"}
    s.update(extra)
    config.save({"propresenter": s}, replace=("propresenter",))


async def until(cond, timeout=6.0, what=""):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.03)
    return False


class Logs(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelno, record.getMessage()))


logs = Logs()
logging.getLogger("relay.propresenter").addHandler(logs)
logging.getLogger("relay.propresenter").setLevel(logging.INFO)

# ------------------------------------------------------------------------------------------------ settings
print("settings")
eq("defaults: off, no port assumed, view-only off", {k: config.get()["propresenter"][k] for k in
                                                      ("enabled", "host", "port", "view_only")},
   {"enabled": False, "host": "", "port": 0, "view_only": False})
eq("defaults: the public phone viewer's lyrics are off", config.get()["propresenter"]["lyrics"],
   {"viewer": False, "screen": True, "overlay": False, "panels": True})
eq("a URL is not a host", pp.clean_host("http://10.0.0.1:50001/"), "")
eq("a bare host is", pp.clean_host(" Studio-Mac.local "), "Studio-Mac.local")
eq("a port outside 1-65535 is dropped", (pp.clean_port(0), pp.clean_port(70000), pp.clean_port("50402")), (0, 0, 50402))
check("nothing is followed until it is switched on and has an address", not pp.configured())
check("the stream's path list is accepted by a live install (46 names)", set(pp.STREAM_PATHS) <= ACCEPTED)
eq("the stream's path list is the fixed one", pp.STREAM_PATHS,
   ["status/slide", "presentation/current", "presentation/slide_index", "timers/current", "stage/message",
    "status/layers", "playlist/active", "look/current", "announcement/active", "announcement/slide_index",
    "status/screens", "status/audience_screens", "status/stage_screens"])

# --------------------------------------------------------------------------------------------------- framing
print("framing")
sp = pp.Splitter()
whole = b'{"url":"status/slide","data":{"current":{"text":"a\\nb"}}}\r\n\r\n{"url":"stage/message","data":""}\r\n\r\n'
got = []
for i in range(0, len(whole), 7):        # cut anywhere, including inside the blank line
    got += sp.feed(whole[i:i + 7])
eq("messages are cut at the blank line however the bytes arrive", [m["url"] for m in got],
   ["status/slide", "stage/message"])
eq("data survives the cuts", got[0]["data"]["current"]["text"], "a\nb")
eq("a message that is not JSON is skipped", [m["url"] for m in pp.Splitter().feed(b"nope\r\n\r\n" + whole)],
   ["status/slide", "stage/message"])
eq("an unfinished message waits", pp.Splitter().feed(b'{"url":"status/sl'), [])

# ------------------------------------------------------------------------------------------------- the client
print("client")
srv = Server()
fake = srv.fake
c = pp.Client("127.0.0.1", srv.port)
eq("GET /version", c.version()["host_description"], "ProPresenter 22")
try:
    c.read("/v1/nothing")
    check("a 404 is an error", False)
except pp.ProError as exc:
    eq("a 404 says to check Network", (exc.kind, exc.status, "Network" in str(exc)), ("http", 404, True))
try:
    c.open_stream(["status/slide", "status/bogus"])
    check("one unknown path fails the whole stream request", False)
except pp.ProError as exc:
    eq("400 on an unknown stream path", (exc.kind, exc.status), ("http", 400))
    check("and says which path", "status/bogus" in str(exc))
try:
    pp.Client("127.0.0.1", 9, False).version()
    check("a closed port is an error", False)
except pp.ProError as exc:
    eq("a closed port is a network error, quickly", exc.kind, "network")
eq("read() refuses a GET that acts", [pp.is_action_path(p) for p in (
    "/v1/trigger/next", "/v1/clear/layer/slide", "/v1/timers/start", "/v1/timer/x/stop", "/v1/status/slide",
    "/v1/presentation/active/next/trigger", "/v1/stage/message", "/v1/version")],
   [True, True, True, True, False, True, False, False])
try:
    c.read("/v1/trigger/next")
    check("read() of an action path raises", False)
except ValueError:
    check("read() of an action path raises", True)
eq("action() is how a trigger goes out", c.action("/v1/trigger/next"), None)

# ---------------------------------------------------------------------------------------------- view-only
print("view-only")
fake.requests.clear()
vo = pp.Client("127.0.0.1", srv.port, True)
for label, call in (("PUT", lambda: vo.put("/v1/stage/message", "x")), ("POST", lambda: vo.post("/v1/x", [])),
                    ("DELETE", lambda: vo.delete("/v1/stage/message")),
                    ("action (a GET that triggers)", lambda: vo.action("/v1/trigger/next"))):
    try:
        call()
        check(f"view-only refuses {label}", False)
    except pp.ViewOnly as exc:
        check(f"view-only refuses {label}", exc.kind == "view_only")
eq("nothing reached ProPresenter", fake.requests, [])
eq("reads still work in view-only mode", vo.version()["api_version"], "v1")
switch = [False]
live_switch = pp.Client("127.0.0.1", srv.port, lambda: switch[0])
live_switch.put("/v1/stage/message", "ok")
switch[0] = True
try:
    live_switch.put("/v1/stage/message", "no")
    check("the switch is read on every call", False)
except pp.ViewOnly:
    check("the switch is read on every call", True)
eq("the stage message is a bare JSON string on the wire", [r[2] for r in fake.requests if r[0] == "PUT"], [b'"ok"'])
fake.stage = ""

# ------------------------------------------------------------------------------------------------ follower
print("follower")


async def scenario_follow():
    fake.song()
    configure(srv)
    streams_before = fake.stream_count
    f = pp.Follower()
    feed = pp.LyricsFeed(f)
    f.start()
    check("connects and is up", await until(lambda: f.up))
    check("the first read of the slide arrives with the connection", await until(lambda: f.lyrics()["text"]))
    ly = f.lyrics()
    eq("presentation name is trimmed", ly["title"], "Great Song")
    eq("a presentation in the Songs library is a song", ly["song"], True)
    eq("current and next slide text", (ly["text"], ly["next"]), ("line one\nline two", "line three"))
    eq("the part the slide is in", (ly["part"], ly["index"]), ("Verse 1", 0))
    eq("one stream, with the fixed path list", (fake.stream_count - streams_before, fake.stream_bodies[-1]),
       (1, pp.STREAM_PATHS))

    # a slide change that is pushed
    fake.index = 1
    fake.push("status/slide", "presentation/slide_index")
    check("a pushed slide change shows", await until(lambda: f.lyrics()["text"] == "line three"))
    eq("the part follows the slide", f.lyrics()["part"], "Verse 1")
    # one that is not pushed at all (the stream misses changes)
    fake.index = 2
    check("a slide change with no push is found by the re-read", await until(lambda: f.lyrics()["text"] == "chorus a"))
    check("the part changes at the group boundary", await until(lambda: f.lyrics()["part"] == "Chorus"))
    eq("the last slide has no next", f.lyrics()["next"], "")
    # the same text pushed twice does nothing
    await asyncio.sleep(0.5)   # a delayed push from the change above may still be on its way
    rev = f.rev
    fake.push("status/slide", "status/slide", "presentation/slide_index")
    await asyncio.sleep(0.6)
    eq("the same text again changes nothing (no new revision, no new message)", f.rev, rev)

    # a feed frame: whole state
    q = feed.subscribe("screen")
    first = json.loads(q.get_nowait().decode()[len("data: "):])
    eq("the feed's message shape", sorted(first), ["enabled", "index", "live", "next", "part", "rev", "server_time",
                                                   "song", "text", "title", "type"])
    eq("it carries the lyrics", (first["type"], first["enabled"], first["live"], first["text"]),
       ("lyrics", True, True, "chorus a"))

    # clear: presentation null, but status/slide keeps the old text
    fake.clear()
    fake.push("presentation/current", "presentation/slide_index")
    check("clear: no presentation, no lyrics, whatever status/slide says", await until(lambda: not f.lyrics()["live"]))
    eq("and the text is empty", (f.lyrics()["text"], f.lyrics()["title"]), ("", ""))
    check("the screen was told", await until(lambda: not q.empty()))
    last = None
    while not q.empty():
        last = json.loads(q.get_nowait().decode()[len("data: "):])
    eq("the feed says not live", (last["live"], last["text"]), (False, ""))

    # a clear nobody pushed
    fake.song("Second Song")
    fake.push("presentation/current", "status/slide", "presentation/slide_index")
    check("a new presentation shows", await until(lambda: f.lyrics()["title"] == "Second Song"))
    fake.clear()
    check("a clear with no push is found by the re-read", await until(lambda: not f.lyrics()["live"]))

    # not a song
    fake.song("Sermon Slides", library="Media", groups=[("Slide", ["Point one", "Point two"])])
    fake.push("presentation/current")
    check("a presentation outside the library, with no song part names, is not a song",
          await until(lambda: f.presentation and f.presentation["name"] == "Sermon Slides"
                      and f.current["text"] == "Point one"))
    eq("songs-only keeps its slides off the lyrics feed", (f.is_song(), f.lyrics()["live"], f.lyrics()["text"]),
       (False, False, ""))
    configure(srv, songs_only=False)
    eq("with songs-only off it shows", (f.lyrics()["live"], f.lyrics()["text"]), (True, "Point one"))
    configure(srv, songs_only=True, songs_library="Media")
    eq("the library name is the operator's", f.is_song(), True)
    configure(srv)
    fake.song("Part Names Only", library="Elsewhere")
    fake.push("presentation/current")
    check("Verse / Chorus group names also mark a song",
          await until(lambda: f.presentation and f.presentation["name"] == "Part Names Only" and f.is_song()))

    # the stage message, from ProPresenter's side and Relay's
    fake.stage = "typed in Pro"
    fake.push("stage/message")
    check("the stage message is followed", await until(lambda: f.stage_message == "typed in Pro"))
    fake.requests.clear()
    await f.set_stage_message("Sound desk needed")
    eq("PUT /v1/stage/message with a bare JSON string",
       [(r[0], r[1], r[2]) for r in fake.requests if r[0] == "PUT"], [("PUT", "/v1/stage/message", b'"Sound desk needed"')])
    eq("and ProPresenter has it", fake.stage, "Sound desk needed")
    await f.clear_stage_message()
    eq("DELETE clears it", (fake.stage, [r[0] for r in fake.requests if r[0] == "DELETE"]), ("", ["DELETE"]))
    # view-only: switched on in config, takes effect on the live client at once
    config.save({"propresenter": {**config.get()["propresenter"], "view_only": True}}, replace=("propresenter",))
    fake.requests.clear()
    try:
        await f.set_stage_message("nope")
        check("view-only refuses the stage message", False)
    except pp.ViewOnly:
        check("view-only refuses the stage message", True)
    try:
        await f.clear_stage_message()
        check("view-only refuses the clear", False)
    except pp.ViewOnly:
        check("view-only refuses the clear", True)
    check("and nothing was sent", not [r for r in fake.requests if r[0] in ("PUT", "DELETE")])
    check("reading carries on in view-only mode", f.up and f.presentation is not None)
    configure(srv)

    # the feed's switches
    config.save({"propresenter": {**config.get()["propresenter"], "lyrics": {"viewer": False, "screen": True,
                                                                              "overlay": False, "panels": True}}},
                replace=("propresenter",))
    check("screen allowed", feed.allowed("screen"))
    check("panels allowed", feed.allowed("panel"))
    check("the phone viewer is not", not feed.allowed("viewer"))
    check("the overlay is opt-in", not feed.allowed("overlay"))
    eq("a refused audience gets nothing but enabled:false", {k: v for k, v in feed.payload("viewer").items()
                                                             if k != "server_time"},
       {"type": "lyrics", "enabled": False, "live": False})
    f.cancel()
    await asyncio.sleep(0.1)


asyncio.run(scenario_follow())

# -------------------------------------------------------------------------------------------------- health
print("health")


async def scenario_health():
    logs.lines.clear()
    fake.song()
    fake.beat = True
    configure(srv)
    before = fake.stream_count
    f = pp.Follower()
    f.start()
    check("up", await until(lambda: f.up))
    # the heartbeat stops: the connection is dropped and reopened
    fake.beat = False
    check("a stalled heartbeat reconnects", await until(lambda: fake.stream_count >= before + 2, 8))
    check("one stall is one miss: still up, nothing announced", f.up and not [l for l in logs.lines if l[0] >= 30])
    fake.beat = True
    check("and it carries on", await until(lambda: f.up and f.misses == 0))
    # ProPresenter refuses: two misses make it down, announced once
    fake.refuse = True
    fake.kill.set()          # drop the live connection
    await asyncio.sleep(0.2)
    fake.kill.clear()
    check("two misses in a row mark it down", await until(lambda: not f.up, 8))
    eq("and the lyrics are gone", f.lyrics()["live"], False)
    await asyncio.sleep(1.2)
    downs = [l for l in logs.lines if l[0] == 30 and "not answering" in l[1]]
    eq("the outage was announced once, however long it lasts", len(downs), 1)
    fake.refuse = False
    check("recovery", await until(lambda: f.up, 8))
    ups = [l for l in logs.lines if "is back" in l[1]]
    eq("and announced once", len(ups), 1)
    check("the slide is read again after it", await until(lambda: f.lyrics()["live"]))
    # an address that does not answer: not up, with a reason
    config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": 9, "songs_library": "Songs"}},
                replace=("propresenter",))
    await f.poke()
    check("a dead address is down with a reason", await until(lambda: not f.up and "Could not reach" in f.error, 8))
    # switched off: quiet
    config.save({"propresenter": {"enabled": False, "host": "127.0.0.1", "port": srv.port}}, replace=("propresenter",))
    await f.poke()
    check("switched off: idle", await until(lambda: f.client is None and not f.up, 4))
    f.cancel()
    await asyncio.sleep(0.1)


asyncio.run(scenario_health())

# -------------------------------------------------------------------------------------------------- Alerts
print("alerts")
bad = None
try:
    alerts.save({"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [], "screens": [], "stage": "yes"}]})
except ValueError as exc:
    bad = str(exc)
check("stage must be a bool", bad and "stage" in bad)
saved = alerts.save({"auto_stop_s": 60, "buttons": [{"name": "Sound desk", "panels": [], "screens": ["Booth"],
                                                    "stage": True},
                                                   {"name": "Coffee", "panels": [], "screens": ["Booth"]}]})
eq("the stage flag is kept; off by default", [b["stage"] for b in saved["alerts"]["buttons"]], [True, False])


async def scenario_alerts():
    fake.song()
    configure(srv)
    f = pp.Follower()
    f.start()
    check("up", await until(lambda: f.up))
    alerts.on_stage = lambda alert_id, text: (f.alert_ended(alert_id) if text is None else f.alert_started(alert_id, text))
    sound = next(b for b in alerts.settings()["buttons"] if b["name"] == "Sound desk")
    coffee = next(b for b in alerts.settings()["buttons"] if b["name"] == "Coffee")
    alerts.press(coffee["id"], "Kitchen")
    await asyncio.sleep(0.4)
    eq("a button without the option leaves the stage display alone", fake.stage, "")
    alerts.press(sound["id"], "Drums")
    check("an alert on a stage button shows on the stage display", await until(lambda: fake.stage != ""))
    eq("with who it is from", fake.stage, "Sound desk (from Drums)")
    aid = alerts.latest[sound["id"]].id
    alerts.ack(aid, screen="Booth")
    check("acknowledging clears it", await until(lambda: fake.stage == ""))
    # an operator's own message is not wiped by an older alert ending
    alerts.press(sound["id"], "Bass")
    check("again", await until(lambda: fake.stage == "Sound desk (from Bass)"))
    await f.set_stage_message("Operator says hi")
    alerts.cancel(sound["id"])
    await asyncio.sleep(0.5)
    eq("an alert ending does not clear someone else's message", fake.stage, "Operator says hi")
    await f.clear_stage_message()
    # view-only: the alert still flashes elsewhere; the stage display is just left alone
    config.save({"propresenter": {**config.get()["propresenter"], "view_only": True}}, replace=("propresenter",))
    fake.requests.clear()
    alerts.press(sound["id"], "Keys")
    await asyncio.sleep(0.5)
    eq("view-only: the alert starts, the stage display is not touched",
       (alerts.latest[sound["id"]].state, [r for r in fake.requests if r[0] in ("PUT", "DELETE")]), ("on", []))
    alerts.cancel(sound["id"])
    configure(srv)
    f.cancel()
    await asyncio.sleep(0.1)


asyncio.run(scenario_alerts())
alerts.on_stage = None

# ------------------------------------------------------------------------------------------- admin routes
print("admin")
eq("admin needs a sign-in", TestClient(app, base_url="http://relay.local:8001").get("/api/admin/propresenter").status_code, 401)
r = admin.post("/api/admin/propresenter", json={"host": "http://10.0.0.5:50001/"})
eq("a URL as the host is refused with a sentence", (r.status_code, "no http://" in r.json()["error"]), (400, True))
config.save({"propresenter": {}}, replace=("propresenter",))
r = admin.post("/api/admin/propresenter", json={"enabled": True, "host": "10.0.0.5"})
eq("switching on with no port is refused", (r.status_code, "port" in r.json()["error"].lower()), (400, True))
r = admin.post("/api/admin/propresenter", json={"host": "10.0.0.5", "port": 70000})
eq("a bad port is refused", r.status_code, 400)
r = admin.post("/api/admin/propresenter/test", json={"host": "127.0.0.1", "port": srv.port})
eq("Test connection reads /version", (r.status_code, r.json()["version"]["host_description"]), (200, "ProPresenter 22"))
r = admin.post("/api/admin/propresenter/test", json={"host": "127.0.0.1", "port": 9})
eq("Test connection on a dead port says so", (r.status_code, "Could not reach" in r.json()["error"]), (502, True))
r = admin.post("/api/admin/propresenter/test", json={"host": "", "port": 0})
eq("Test connection needs an address", r.status_code, 400)
r = admin.post("/api/admin/propresenter", json={"enabled": False, "host": "127.0.0.1", "port": srv.port,
                                                "lyrics": {"overlay": True}})
body = r.json()
eq("save keeps the host and port", (r.status_code, body["settings"]["host"], body["settings"]["port"]),
   (200, "127.0.0.1", srv.port))
eq("a lyrics switch moves alone", body["settings"]["lyrics"], {"viewer": False, "screen": True, "overlay": True,
                                                              "panels": True})
eq("the settings are in the admin state and the file", admin.get("/api/admin/state").json()["config"]["propresenter"]["host"],
   "127.0.0.1")
eq("while off, the state says not configured", body["state"]["configured"], False)
# the stream refuses what is switched off
admin.post("/api/admin/propresenter", json={"enabled": True})
eq("the phone viewer is refused by default", viewer.get("/stream?stream=lyrics").status_code, 403)
eq("so is an unknown audience", viewer.get("/stream?stream=lyrics&for=tv").status_code, 400)
admin.post("/api/admin/propresenter", json={"lyrics": {"screen": False, "overlay": False, "panels": False}})
eq("every audience can be switched off", [viewer.get(f"/stream?stream=lyrics&for={a}").status_code
                                          for a in ("screen", "overlay", "panel")], [403, 403, 403])
r = admin.post("/api/admin/propresenter", json={"enabled": False})
eq("nothing is offered while ProPresenter is off", viewer.get("/stream?stream=lyrics&for=screen").status_code, 403)
# stage routes: view-only maps to 409
config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": srv.port, "view_only": True}},
            replace=("propresenter",))
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: True)
fake.requests.clear()
r = admin.post("/api/admin/propresenter/stage", json={"text": "hello"})
eq("the admin stage message is refused in view-only mode", (r.status_code, "View-only" in r.json()["error"]), (409, True))
r = admin.request("DELETE", "/api/admin/propresenter/stage")
eq("and so is the clear", r.status_code, 409)
eq("nothing reached ProPresenter", [x for x in fake.requests if x[0] in ("PUT", "DELETE")], [])
config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": srv.port, "view_only": False}},
            replace=("propresenter",))
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: bool(pp.settings().get("view_only")))
r = admin.post("/api/admin/propresenter/stage", json={"text": "  Sound   desk needed  "})
eq("the admin stage message goes out tidied", (r.status_code, fake.stage), (200, "Sound desk needed"))
r = admin.post("/api/admin/propresenter/stage", json={"text": "  "})
eq("an empty message is refused", r.status_code, 400)
r = admin.request("DELETE", "/api/admin/propresenter/stage")
eq("the admin clear", (r.status_code, fake.stage), (200, ""))
pp.follower.client = None

# --------------------------------------------------------------------------------- live state (stage 4)
print("live state")


async def scenario_state():
    fake.song(groups=[("Verse 1", ["v1 a", "v1 b"]), ("Chorus", ["chorus a"])])
    fake.presentation["arrangement"] = ["Verse 1", "Chorus", "Verse 1", "Chorus"]
    fake.index = 2
    configure(srv)
    f = pp.Follower()
    f.start()
    check("connects", await until(lambda: f.up))
    check("the item is read", await until(lambda: f.item() is not None and f.item()["part"] == "Chorus"))
    it = f.item()
    eq("item title, slide number and the part with its colour", (it["title"], it["slide_number"], it["slide_count"],
                                                                  it["part"], it["part_color"]),
       ("Great Song", 3, 6, "Chorus", "#ff0080"))
    eq("the next slide's part is the repeated verse", (it["next"]["part"], it["next"]["part_color"]), ("Verse 1", "#ff0080"))
    eq("notes of this and the next slide travel with the item", (it["notes"], it["next"]["notes"]), ("", ""))
    fake.stale = None
    st = f.state()
    eq("the admin state carries the item, announcement and screens", sorted(k for k in ("item", "announcement", "screens")
                                                                          if k in st), ["announcement", "item", "screens"])
    eq("no announcement is up", st["announcement"], None)
    eq("screens come from status/screens and the two switches", (st["screens"]["audience"], st["screens"]["stage"],
                                                                 st["screens"]["list"]),
       (False, False, [{"uuid": "SCR", "name": "Screen", "type": "audience", "width": 1920, "height": 1080}]))
    # a presentation with a slide's notes
    orig = fake.status_slide
    fake.status_slide = lambda: {"current": {"text": "chorus a", "notes": "Key of G", "uuid": "x"},
                                 "next": {"text": "v1 a", "notes": "Softly", "uuid": "y"}}
    check("slide notes of this and the next slide are followed", await until(lambda: f.item()["notes"] == "Key of G"))
    eq("both are in the item", (f.item()["notes"], f.item()["next"]["notes"]), ("Key of G", "Softly"))
    fake.status_slide = orig

    # announcements: pushed, and not pushed
    fake.ann_index = 1   # the position first: it reads as "none" until an announcement exists
    fake.announcement = {"uuid": "A1", "name": "Lobby loop", "groups": [("Welcome", ["Hello", "Nursery is open"])]}
    fake.push("announcement/active", "announcement/slide_index")
    check("a pushed announcement shows", await until(lambda: f.announcement_state() is not None
                                                     and f.announcement_state()["slide_number"] == 2))
    a = f.announcement_state()
    eq("title, slide number, part, text and notes", (a["title"], a["slide_number"], a["slide_count"], a["part"],
                                                      a["text"], a["notes"]),
       ("Lobby loop", 2, 2, "Welcome", "Nursery is open", "ann note Nursery is open"))
    fake.ann_index = 0
    check("an announcement slide change with no push is found by the re-read",
          await until(lambda: f.announcement_state()["slide_number"] == 1))
    fake.announcement = {"uuid": "A2", "name": "Second loop", "groups": [("Info", ["Parking"])]}
    check("a different announcement with no push is found too", await until(lambda: f.announcement_state()
                                                                           and f.announcement_state()["title"] == "Second loop"))
    fake.push("announcement/slide_index")
    await asyncio.sleep(0.5)
    check("a pushed position alone does not lose the announcement", f.announcement_state() is not None)
    fake.announcement = None
    fake.ann_index = None
    check("a cleared announcement goes away", await until(lambda: f.announcement_state() is None))

    # screens
    fake.screens["stage"] = True
    check("screens turned on in ProPresenter are found (the stream may miss it)",
          await until(lambda: f.screens()["stage"] is True))
    fake.screens["audience"] = True
    fake.push("status/audience_screens")
    check("and a pushed one at once", await until(lambda: f.screens()["audience"] is True))
    fake.screens.update(audience=False, stage=False)
    check("and off again", await until(lambda: f.screens()["audience"] is False and f.screens()["stage"] is False))

    # the playlist item's own name
    f.data["playlist/active"] = {"presentation": {"playlist": {"uuid": "PL3", "name": "Setlist", "index": 2},
                                                  "item": {"uuid": "S1", "name": "Great Song (opener)", "index": 1}}}
    eq("the playlist and its item are named in the item details", (f.item()["playlist"], f.item()["playlist_item"]),
       ("Setlist", {"name": "Great Song (opener)", "index": 1}))
    # down: nothing stale
    f.client = None
    f._miss("x"); f._miss("x")
    eq("when ProPresenter goes away the announcement and item go too", (f.item(), f.announcement_state()), (None, None))
    f.cancel()
    await asyncio.sleep(0.1)
    fake.clear()
    fake.announcement = None


asyncio.run(scenario_state())

# ------------------------------------------------------------------------------------------------ control
print("control")
from app.modules.propresenter.ppcontrol import control as ctl

fake.song(groups=[("Verse 1", ["v1 a", "v1 b"]), ("Chorus", ["chorus a"])])
fake.presentation["arrangement"] = ["Verse 1", "Chorus", "Verse 1", "Chorus"]
config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": srv.port, "view_only": False,
                              "macros_mode": "selected", "macros_allowed": ["M1"]}}, replace=("propresenter",))
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: bool(pp.settings().get("view_only")))
pp.follower.presentation = {"uuid": fake.presentation["uuid"], "name": "Great Song", "path": "", "library": "Songs",
                            "groups": []}
B = "/api/admin/propresenter"


def sent(kind=None):
    return [(m, p, b) for m, p, b in fake.requests if kind is None or m == kind]


# -- token protection: every control route is admin-only
anon = TestClient(app, base_url="http://relay.local:8001")
routes = [("GET", "/slides"), ("GET", "/thumbnail/P-Grea/0"), ("POST", "/step"), ("POST", "/next-presentation"),
          ("POST", "/slide"), ("POST", "/clear"), ("GET", "/playlists"), ("GET", "/playlists/PL1"),
          ("POST", "/playlists/PL1/trigger"), ("GET", "/macros"), ("POST", "/macros/M1/trigger"), ("GET", "/looks"),
          ("POST", "/looks/L1/trigger"), ("GET", "/timers"), ("POST", "/timers/T1/start"), ("GET", "/messages"),
          ("POST", "/messages/MSG1/trigger"), ("POST", "/messages/MSG1/clear"), ("POST", "/transport"),
          ("POST", "/item"), ("POST", "/screens"), ("GET", "/playlists/PL3/diagnostics"),
          ("GET", "/playlists/PL3/items/1/cues"), ("GET", "/playlists/PL3/thumbnail/1/0")]
fake.requests.clear()
codes = {(m, p): anon.request(m, B + p, json={} if m == "POST" else None).status_code for m, p in routes}
eq("no control route answers without signing in", sorted(set(codes.values())), [401])
eq("and none reached ProPresenter", fake.requests, [])

# -- roles: an operator controls, a viewer reads, only an admin changes the setup
operator = _db.client(app, "operator", base_url="http://relay.local:8001")
viewer_user = _db.client(app, "viewer", base_url="http://relay.local:8001")
fake.requests.clear()
eq("an operator steps the slides", operator.post(B + "/step", json={"direction": "next"}).status_code, 200)
eq("an operator triggers a macro, a look and a message",
   [operator.post(B + p, json={}).status_code for p in ("/macros/M1/trigger", "/looks/L1/trigger", "/messages/MSG1/clear")],
   [200, 200, 200])
eq("an operator reads the playlists and slides", [operator.get(B + p).status_code for p in ("/playlists", "/slides")], [200, 200])
eq("an operator may not change the setup", operator.post(B, json={"view_only": True}).status_code, 403)
eq("…nor test a connection, search the network or edit the link",
   [operator.post(B + p, json={}).status_code for p in ("/test", "/discover", "/link/mapping", "/link/playlist")], [403] * 4)
eq("…nor list every macro", operator.get(B + "/macros?all=true").status_code, 403)
eq("…and the setup is unchanged", config.get()["propresenter"]["view_only"], False)
fake.requests.clear()
eq("a viewer reads the state, the slides, the playlists and the live view",
   [viewer_user.get(B + p).status_code for p in ("", "/slides", "/playlists", "/macros", "/live")], [200] * 5)
eq("a viewer may not control",
   [viewer_user.post(B + p, json={}).status_code for p in ("/step", "/clear", "/stage", "/playlists/PL1/trigger",
                                                           "/timers/T1/start", "/lobby/start", "/transport")], [403] * 7)
eq("a viewer may not change the setup", [viewer_user.post(B, json={"view_only": True}).status_code,
                                         viewer_user.delete(B + "/stage").status_code], [403, 403])
eq("a refused request never reached ProPresenter",
   [p for _, p, _ in sent("GET") if "trigger" in p or "clear" in p or "next" in p], [])

# -- next / previous / next presentation / a slide / clears
fake.requests.clear()
eq("next", admin.post(B + "/step", json={"direction": "next"}).status_code, 200)
admin.post(B + "/step", json={"direction": "previous"})
admin.post(B + "/next-presentation")
eq("next, previous and the next item hit the right paths", [p for _, p, _ in sent("GET")],
   ["/v1/trigger/next", "/v1/trigger/previous", "/v1/playlist/focused/next/trigger"])
eq("a bad direction is refused", admin.post(B + "/step", json={"direction": "sideways"}).status_code, 400)
fake.requests.clear()
admin.post(B + "/slide", json={"uuid": "P-Grea", "index": 3})
eq("a slide is triggered by presentation and index", [p for _, p, _ in sent("GET")], ["/v1/presentation/P-Grea/3/trigger"])
eq("a path-shaped uuid is refused", admin.post(B + "/slide", json={"uuid": "../clear/layer/slide", "index": 0}).status_code, 400)
eq("so is a bad index", admin.post(B + "/slide", json={"uuid": "P-Grea", "index": "x"}).status_code, 400)
eq("and a negative one", admin.post(B + "/slide", json={"uuid": "P-Grea", "index": -1}).status_code, 400)
fake.requests.clear()
for layer in ("slide", "media", "props", "messages", "audio", "video_input", "announcements"):
    admin.post(B + "/clear", json={"layer": layer})
eq("each layer clears by its own path", [p for _, p, _ in sent("GET")],
   [f"/v1/clear/layer/{x}" for x in ("slide", "media", "props", "messages", "audio", "video_input", "announcements")])
eq("an unknown layer is refused", admin.post(B + "/clear", json={"layer": "everything"}).status_code, 400)

# -- the slide grid follows the arrangement, with group names and colours
r = admin.get(B + "/slides").json()
eq("the grid has a cue for every play of a repeated group", [(c["index"], c["group"]) for c in r["cues"]],
   [(0, "Verse 1"), (1, "Verse 1"), (2, "Chorus"), (3, "Verse 1"), (4, "Verse 1"), (5, "Chorus")])
eq("each cue carries its group's colour", r["cues"][0]["color"], "#ff0080")
rev = r["rev"]
fake.presentation["groups"][0] = ("Verse 1", ["v1 a", "v1 CHANGED"])
eq("the change marker moves when a slide changes", admin.get(B + "/slides").json()["rev"] != rev, True)
fake.presentation["groups"][0] = ("Verse 1", ["v1 a", "v1 b"])
eq("and returns when it is put back", admin.get(B + "/slides").json()["rev"], rev)
eq("the follower's part name follows the arrangement too", pp.Follower._describe("u", "n", fake.detail("u")["presentation"])["groups"],
   [("Verse 1", 2), ("Chorus", 1), ("Verse 1", 2), ("Chorus", 1)])

# -- thumbnails: proxied, cached by presentation + index + change marker
fake.thumb_calls = 0
t1 = admin.get(f"{B}/thumbnail/P-Grea/2?v={rev}")
eq("a thumbnail is served as an image", (t1.status_code, t1.headers["content-type"], t1.content[:2]), (200, "image/jpeg", b"\xff\xd8"))
t2 = admin.get(f"{B}/thumbnail/P-Grea/2?v={rev}")
eq("asking again is a cache hit: ProPresenter was asked once", (t2.content == t1.content, fake.thumb_calls), (True, 1))
admin.get(f"{B}/thumbnail/P-Grea/3?v={rev}")
eq("another index is another fetch", fake.thumb_calls, 2)
admin.get(f"{B}/thumbnail/P-Grea/2?v=other")
eq("a new change marker fetches again", fake.thumb_calls, 3)
eq("a browser never needs ProPresenter's own thumbnail path", [p for _, p, _ in sent("GET") if "thumbnail" in p][0].startswith("/v1/presentation/P-Grea/thumbnail/2"), True)
eq("a bad thumbnail id is refused", admin.get(B + "/thumbnail/..%2Fx/0").status_code in (400, 404), True)
ctl.thumbs.clear(); ctl.thumb_bytes = 0
pp.follower.client = pp.Client("127.0.0.1", 1, False)      # nothing listens there
r = admin.get(B + "/thumbnail/P-Grea/9?v=z")
eq("a thumbnail while ProPresenter is down is a clear 502", (r.status_code, "Could not reach" in r.json()["error"]), (502, True))
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: bool(pp.settings().get("view_only")))

# -- playlists
r = admin.get(B + "/playlists").json()
eq("playlists come as a tree (folders hold playlists)", [(p["name"], p["type"], [c["name"] for c in p["children"]]) for p in r["playlists"]],
   [("Sunday", "playlist", []), ("Folder", "group", ["Youth"])])
r = admin.get(B + "/playlists/PL1").json()
eq("a playlist lists its items", [(i["index"], i["name"], i["type"], i["presentation"]) for i in r["items"]],
   [(0, "Welcome", "header", ""), (1, "Great Song", "presentation", "P-Grea")])
fake.requests.clear()
admin.post(B + "/playlists/PL1/trigger", json={"index": 1})
eq("an item is triggered by playlist and index", [p for _, p, _ in sent("GET")], ["/v1/playlist/PL1/1/trigger"])

# -- macros: the operator-safe selection is enforced on the server
r = admin.get(B + "/macros").json()
eq("only the selected macros are listed", [m["name"] for m in r["macros"]], ["Lights up"])
eq("the picker lists them all", [(m["name"], m["selected"]) for m in admin.get(B + "/macros?all=true").json()["macros"]],
   [("Lights up", True), ("Reboot", False)])
fake.requests.clear()
eq("a selected macro triggers by its ID", admin.post(B + "/macros/M1/trigger").status_code, 200)
eq("one outside the selection is refused", admin.post(B + "/macros/M2/trigger").status_code, 403)
eq("and never reached ProPresenter", [p for _, p, _ in sent("GET")], ["/v1/macro/M1/trigger"])
admin.post(B, json={"macros_mode": "all"})
eq("with 'all' every macro is listed", len(admin.get(B + "/macros").json()["macros"]), 2)
eq("and may be triggered", admin.post(B + "/macros/M2/trigger").status_code, 200)
r = admin.post(B, json={"macros_mode": "bogus", "macros_allowed": ["M1", "../x", 5]})
eq("a bad mode and bad IDs are cleaned on save", (r.json()["settings"]["macros_mode"], r.json()["settings"]["macros_allowed"]),
   ("selected", ["M1"]))

# -- looks
eq("looks list", [l["name"] for l in admin.get(B + "/looks").json()["looks"]], ["Default", "Sermon"])
fake.requests.clear()
admin.post(B + "/looks/L2/trigger")
eq("a look is triggered by ID", [p for _, p, _ in sent("GET")], ["/v1/look/L2/trigger"])

# -- timers: one at a time, never the bulk call
eq("timers list", [(t["name"], t["kind"]) for t in admin.get(B + "/timers").json()["timers"]], [("Segment", "countdown")])
fake.requests.clear()
for op in ("start", "stop", "reset"):
    admin.post(B + f"/timers/T1/{op}")
eq("each operation goes to the one timer", [p for _, p, _ in sent("GET")], ["/v1/timer/T1/start", "/v1/timer/T1/stop", "/v1/timer/T1/reset"])
eq("an unknown operation is refused", admin.post(B + "/timers/T1/explode").status_code, 400)
for bulk in ("/v1/timers/start", "/v1/timers/stop", "/v1/timers/reset", "/v1/capture/start"):
    try:
        pp.follower.client.action(bulk)
        check("the client never sends " + bulk, False)
    except ValueError:
        check("the client never sends " + bulk, True)
eq("no bulk timer path was sent", [p for _, p, _ in fake.requests if p.startswith("/v1/timers/")], [])

# -- messages
r = admin.get(B + "/messages").json()["messages"]
eq("messages list with their tokens", [(m["name"], m["tokens"], m["active"]) for m in r],
   [("Nursery", [{"name": "room", "kind": "text", "text": "3"}], False)])
fake.requests.clear()
r = admin.post(B + "/messages/MSG1/trigger", json={"tokens": [{"name": "room", "text": "  Room   12 "}]})
eq("a message is triggered with its text tokens", (r.status_code, [(m, p, json.loads(b)) for m, p, b in sent("POST")]),
   (200, [("POST", "/v1/message/MSG1/trigger", [{"name": "room", "text": {"text": "Room 12"}}])]))
eq("it then reads as active", admin.get(B + "/messages").json()["messages"][0]["active"], True)
fake.requests.clear()
admin.post(B + "/messages/MSG1/clear")
eq("and clears with a GET to its clear path", [p for _, p, _ in sent("GET")], ["/v1/message/MSG1/clear"])
eq("a token with no name is refused", admin.post(B + "/messages/MSG1/trigger", json={"tokens": [{"text": "x"}]}).status_code, 400)

# -- transport
fake.requests.clear()
r = admin.post(B + "/transport", json={"seconds": 12.5})
eq("go to a time is a PUT of a bare number", (r.status_code, [(m, p, json.loads(b)) for m, p, b in sent("PUT")]),
   (200, [("PUT", "/v1/transport/presentation/time", 12.5)]))
eq("a bad time is refused", [admin.post(B + "/transport", json={"seconds": v}).status_code for v in ("x", -1, None)], [400, 400, 400])

# -- next / previous playlist ITEM (Shift + arrow keys, and the buttons)
fake.requests.clear()
admin.post(B + "/item", json={"direction": "next"})
admin.post(B + "/item", json={"direction": "previous"})
eq("with no active playlist, next and previous item go to the focused playlist (never the slide-level trigger)",
   [p for _, p, _ in sent("GET")], ["/v1/playlist/focused/next/trigger", "/v1/playlist/focused/previous/trigger"])
fake.no_focus = True
r = admin.post(B + "/item", json={"direction": "next"})
eq("with no playlist focused, ProPresenter's 404 becomes a plain sentence", (r.status_code, r.json()["error"]),
   (404, "No playlist is active or focused in ProPresenter. Trigger an item first."))
fake.no_focus = False
pp.follower.data["playlist/active"] = {"presentation": {"playlist": {"uuid": "PL3", "name": "Setlist", "index": 2},
                                                        "item": {"uuid": "S1", "name": "Great Song", "index": 1}}}
fake.requests.clear()
admin.post(B + "/item", json={"direction": "next"})
admin.post(B + "/item", json={"direction": "previous"})
eq("with an active playlist they go to it", [p for _, p, _ in sent("GET")],
   ["/v1/playlist/PL3/next/trigger", "/v1/playlist/PL3/previous/trigger"])
pp.follower.data["playlist/active"] = {"presentation": {"playlist": {"uuid": "../clear/layer/slide"}}}
fake.requests.clear()
eq("an active playlist id that is path-shaped is refused", (admin.post(B + "/item", json={"direction": "next"}).status_code,
                                                            fake.requests), (400, []))
pp.follower.data.pop("playlist/active")
eq("a bad direction for an item is refused", admin.post(B + "/item", json={"direction": "../x"}).status_code, 400)

# -- screens: a bare true or false, through the one client
fake.requests.clear()
r = admin.post(B + "/screens", json={"kind": "audience", "on": True})
eq("audience screens on is a PUT of a bare true", (r.status_code, [(m, p, json.loads(b)) for m, p, b in sent("PUT")]),
   (200, [("PUT", "/v1/status/audience_screens", True)]))
admin.post(B + "/screens", json={"kind": "stage", "on": False})
eq("stage screens off is a PUT of a bare false", [(p, json.loads(b)) for m, p, b in sent("PUT")][-1],
   ("/v1/status/stage_screens", False))
eq("the fake has them", fake.screens, {"audience": True, "stage": False})
fake.screens["audience"] = False
fake.requests.clear()
eq("an unknown screen, or a non-boolean, is refused", [admin.post(B + "/screens", json=b).status_code for b in (
   {"kind": "lobby", "on": True}, {"kind": "audience", "on": "yes"}, {"kind": "audience", "on": 1}, {"kind": "audience"})],
   [400, 400, 400, 400])
eq("and nothing was sent", fake.requests, [])

# -- the cue list: an item's own arrangement
detail = {"groups": [{"uuid": "G-V", "name": "Verse", "slides": [{"text": "v"}]},
                     {"uuid": "G-C", "name": "Chorus", "slides": [{"text": "c1"}, {"text": "c2"}]}],
          "arrangements": [{"id": {"uuid": "A1", "name": "Master"}, "groups": ["G-V", "G-C"]},
                           {"id": {"uuid": "A2", "name": "Chorus twice"}, "groups": ["G-V", "G-C", "G-C"]},
                           {"id": {"uuid": "A3", "name": "Broken"}, "groups": ["G-V", "G-GONE"]}],
          "current_arrangement": "A1"}
names = lambda arr: [c["group"] + str(c["index"]) for c in pp.cue_list(detail, arr)]
eq("no arrangement named: the current one", names(None), ["Verse0", "Chorus1", "Chorus2"])
eq("an arrangement by uuid repeats the chorus at its own place", names("A2"),
   ["Verse0", "Chorus1", "Chorus2", "Chorus3", "Chorus4"])
eq("an arrangement by name", names("Chorus twice"), names("A2"))
eq("an unknown or unusable arrangement falls back to the current one", (names("nope"), names("A3")),
   (names(None), names(None)))

# -- playlist items: every kind, with section markers and the arrangement
r = admin.get(B + "/playlists/PL3").json()
eq("every item is listed, headers and placeholders included",
   [(i["index"], i["type"], i["name"], i["hidden"], i["pco"]) for i in r["items"]],
   [(0, "header", "Worship", False, False), (1, "presentation", "Great Song", False, True),
    (2, "presentation", "Gone Song", True, False), (3, "placeholder", "Empty slot", False, False),
    (4, "presentation", "Unlinked", False, False), (5, "header", "Sermon", False, False)])
eq("a header keeps its colour, a presentation its arrangement", (r["items"][0]["color"], r["items"][1]["arrangement"],
                                                                 r["items"][1]["arrangement_uuid"]),
   ("#0000ff", "Master", "ARR"))
r = admin.get(B + "/playlists/PL3/items/1/cues").json()
eq("an item's cues follow its arrangement: the repeated verse and chorus each have a cue",
   [(c["index"], c["group"]) for c in r["cues"]],
   [(0, "Verse 1"), (1, "Verse 1"), (2, "Chorus"), (3, "Verse 1"), (4, "Verse 1"), (5, "Chorus")])
eq("the strip is for that item", (r["index"], r["name"], r["presentation"], r["arrangement"]),
   (1, "Great Song", "P-Grea", "Master"))
prev_rev = r["rev"]
eq("a header has no slides", admin.get(B + "/playlists/PL3/items/0/cues").status_code, 400)
eq("nor has a placeholder", admin.get(B + "/playlists/PL3/items/3/cues").status_code, 400)
eq("nor an item that does not exist", admin.get(B + "/playlists/PL3/items/99/cues").status_code, 400)
eq("a path-shaped playlist id is refused", admin.get(B + "/playlists/..%2Fclear/items/1/cues").status_code in (400, 404), True)

# -- playlist thumbnails: proxied and cached exactly like the presentation ones
fake.thumb_calls = 0
fake.requests.clear()
t1 = admin.get(f"{B}/playlists/PL3/thumbnail/1/2?v={prev_rev}")
eq("a playlist thumbnail is served as an image", (t1.status_code, t1.headers["content-type"], t1.content[:2]),
   (200, "image/jpeg", b"\xff\xd8"))
eq("ProPresenter was asked at the playlist item's path, for that cue",
   [p for _, p, _ in sent("GET")], ["/v1/playlist/PL3/1/thumbnail/2?quality=320&thumbnail_type=jpeg"])
t2 = admin.get(f"{B}/playlists/PL3/thumbnail/1/2?v={prev_rev}")
eq("asking again is a cache hit", (t2.content == t1.content, fake.thumb_calls), (True, 1))
admin.get(f"{B}/playlists/PL3/thumbnail/1/3?v={prev_rev}")
eq("the second chorus, at its own cue, is its own thumbnail", fake.thumb_calls, 2)
admin.get(f"{B}/playlists/PL3/thumbnail/1/2?v=other")
eq("a new change marker fetches again", fake.thumb_calls, 3)
admin.get(f"{B}/playlists/PL3/thumbnail/2/2?v={prev_rev}")
eq("another item is another fetch", fake.thumb_calls, 4)
eq("a presentation thumbnail with the same numbers is not mistaken for it",
   (admin.get(f"{B}/thumbnail/PL3/1?v={prev_rev}").status_code, fake.thumb_calls), (200, 5))
eq("a bad playlist id or cue is refused", (admin.get(B + "/playlists/..%2Fx/thumbnail/1/0").status_code in (400, 404),
                                            admin.get(B + "/playlists/PL3/thumbnail/x/0").status_code,
                                            admin.get(B + "/playlists/PL3/thumbnail/1/-1").status_code), (True, 422, 400))
pp.follower.client = pp.Client("127.0.0.1", 1, False)
ctl.thumbs.clear(); ctl.thumb_bytes = 0
r = admin.get(B + "/playlists/PL3/thumbnail/1/0?v=z")
eq("a playlist thumbnail while ProPresenter is down is a fixed 502", (r.status_code, "Could not reach" in r.json()["error"]),
   (502, True))
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: bool(pp.settings().get("view_only")))

# -- playlist diagnostics
fake.missing = {"P-GONE"}
fake.requests.clear()
r = admin.get(B + "/playlists/PL3/diagnostics").json()
eq("the diagnostics count the items and their kinds", (r["count"], r["types"], r["hidden"]),
   (6, {"header": 2, "presentation": 3, "placeholder": 1}, 1))
eq("each presentation item says whether its presentation exists",
   [(i["index"], i["status"]) for i in r["items"]],
   [(0, ""), (1, "ok"), (2, "missing"), (3, ""), (4, "none"), (5, "")])
eq("items with no matching presentation are counted", r["problems"], 2)
eq("the uuids are there", (r["items"][1]["uuid"], r["items"][1]["presentation"]), ("S1", "P-Grea"))
eq("each presentation was looked up once, and an empty one not at all",
   sorted(p for _, p, _ in sent("GET") if p.startswith("/v1/presentation/")), ["/v1/presentation/P-GONE", "/v1/presentation/P-Grea"])
fake.missing = set()
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: False)
orig_read = pp.Client.read
def flaky(self, path, timeout=pp.REQUEST_TIMEOUT_S):
    if path.startswith("/v1/presentation/P-Grea"):
        raise pp.ProError("timeout", "ProPresenter did not answer in time.")
    return orig_read(self, path, timeout)
pp.Client.read = flaky
try:
    r = admin.get(B + "/playlists/PL3/diagnostics").json()
    eq("a stalled lookup is 'unknown', never 'missing'", r["items"][1]["status"], "unknown")
finally:
    pp.Client.read = orig_read
pp.follower.client = pp.Client("127.0.0.1", srv.port, lambda: bool(pp.settings().get("view_only")))

# -- view-only: every control write is refused, before ProPresenter is touched; reads still work
config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": srv.port, "view_only": True,
                              "macros_mode": "all"}}, replace=("propresenter",))
fake.requests.clear()
writes = [("/step", {"direction": "next"}), ("/next-presentation", {}), ("/slide", {"uuid": "P-Grea", "index": 0}),
          ("/clear", {"layer": "slide"}), ("/playlists/PL1/trigger", {"index": 1}), ("/macros/M1/trigger", {}),
          ("/looks/L1/trigger", {}), ("/timers/T1/start", {}), ("/messages/MSG1/trigger", {"tokens": []}),
          ("/messages/MSG1/clear", {}), ("/transport", {"seconds": 1}), ("/item", {"direction": "next"}),
          ("/screens", {"kind": "audience", "on": True})]
codes = [admin.post(B + p, json=b).status_code for p, b in writes]
eq("view-only refuses every control write with 409", codes, [409] * len(writes))
eq("and nothing reached ProPresenter", fake.requests, [])
eq("reads still work in view-only mode", (admin.get(B + "/playlists").status_code, admin.get(B + "/slides").status_code,
                                          admin.get(f"{B}/thumbnail/P-Grea/0?v=ro").status_code,
                                          admin.get(B + "/playlists/PL3/diagnostics").status_code,
                                          admin.get(f"{B}/playlists/PL3/thumbnail/1/0?v=ro").status_code),
   (200, 200, 200, 200, 200))
config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": srv.port, "view_only": False}},
            replace=("propresenter",))

# -- ProPresenter down
pp.follower.client = pp.Client("127.0.0.1", 1, False)
r = admin.post(B + "/step", json={"direction": "next"})
eq("with ProPresenter unreachable a control says so, without exception text",
   (r.status_code, r.json()["error"]), (502, "Could not reach ProPresenter. Check the address and port, and that ProPresenter is running."))
pp.follower.client = None
r = admin.post(B + "/step", json={"direction": "next"})
eq("with ProPresenter not set up there is an error, not a crash", r.status_code, 502)
pp.follower.presentation = None
fake.clear()


# ------------------------------------------------------------------------------------------------ panel feeds
print("panel feeds (stage 3)")


def trow(uuid, name, time_text, state):
    return {"id": {"uuid": uuid, "name": name, "index": 0}, "time": time_text, "state": state}


def cfg_row(uuid, name, kind):
    return {"id": {"uuid": uuid, "name": name, "index": 0}, "allows_overrun": True,
            ("elapsed_time" if kind == "up" else "countdown"): {"duration": 300}}


def frames(q):
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait().decode()[len("data: "):]))
    return out


eq("the time ProPresenter writes is read as seconds", [pp.Follower.parse_timer(t) for t in
   ("00:05:00", "00:00:02", "-00:00:02", "1:30", "42", "", "ab:cd", "00:00:00:01", None)],
   [300.0, 2.0, -2.0, 90.0, 42.0, None, None, None, None])
eq("a countdown is ok above amber, amber from it, red from red, over past zero",
   [pp.timer_level(v, s, "down", 120, 30) for v, s in ((300, "running"), (120, "running"), (31, "running"),
                                                     (30, "running"), (0, "running"), (-1, "overrunning"),
                                                     (0, "complete"), (5, "overran"))],
   ["ok", "amber", "amber", "red", "red", "over", "red", "over"])
eq("an elapsed timer has no threshold", pp.timer_level(5000, "running", "up", 120, 30), "ok")
eq("thresholds: red is held to amber, a bad number is dropped, never raised",
   (pp.clean_timer_settings({"amber_s": 90, "red_s": 600}, {"amber_s": 120, "red_s": 30, "show": []}),
    pp.clean_timer_settings({"amber_s": "x", "red_s": -4, "show": "all"}, {"amber_s": 120, "red_s": 30, "show": []})),
   ({"amber_s": 90, "red_s": 90, "show": []}, {"amber_s": 120, "red_s": 30, "show": []}))
eq("a problem is a fixed sentence", [pp.timer_fields(x)[1] for x in (
    {"amber_s": 99999999}, {"red_s": True}, {"show": ["../x"]}, {"show": ["ok-id"], "amber_s": 5})],
   ["The amber threshold is a number of seconds from 0 to 86399.", "The red threshold is a number of seconds from 0 to 86399.",
    "Timers to show is a list of timer IDs.", ""])
eq("hand-edited settings are cleaned on load, not refused",
   pp.clean_stored({"timers": {"amber_s": "soon", "red_s": 10, "show": ["A1", 5, "../no", "A1", "B2"]}})["timers"],
   {"amber_s": 120, "red_s": 10, "show": ["A1", "B2"]})


async def scenario_panel_feeds():
    fake.clear()
    fake.song(groups=[("Verse 1", ["line one\nline two", "line three"]), ("Chorus", ["chorus a"])])
    fake.timer_rows = [trow("T-A", "Segment Countdown", "00:05:00", "stopped"),
                       trow("T-B", "Game Timer", "00:01:00", "stopped")]
    fake.timer_cfg = [cfg_row("T-A", "Segment Countdown", "down"), cfg_row("T-B", "Game Timer", "up")]
    configure(srv)
    f = pp.Follower()
    feed = pp.TimersFeed(f)
    f.start()
    check("connects", await until(lambda: f.up))
    check("the timers are read", await until(lambda: len(f.timers()) == 2))
    check("and which count up is read from the configuration", await until(lambda: f.timer_kinds.get("T-B") == "up"))
    eq("a down timer and an up timer", [(t["name"], t["dir"], t["value"]) for t in f.timers()],
       [("Segment Countdown", "down", 300.0), ("Game Timer", "up", 60.0)])
    calls = fake.timer_cfg_calls
    await asyncio.sleep(0.8)
    check("the configuration is not read every second", fake.timer_cfg_calls - calls <= 1, fake.timer_cfg_calls - calls)

    # -- the message
    q = feed.subscribe("")
    first = frames(q)[0]
    eq("the message shape", sorted(first), ["amber_s", "enabled", "live", "red_s", "rev", "server_time", "timers", "type"])
    eq("it carries every timer with a state, a direction, the seconds and the colour",
       (first["type"], first["enabled"], first["live"], first["amber_s"], first["red_s"],
        [(t["name"], t["state"], t["dir"], t["value"], t["level"]) for t in first["timers"]]),
       ("pptimers", True, True, 120, 30, [("Segment Countdown", "stopped", "down", 300.0, "ok"),
                                            ("Game Timer", "stopped", "up", 60.0, "ok")]))

    # -- a timer starts: one message, then none while it only counts
    fake.timer_rows = [trow("T-A", "Segment Countdown", "00:04:59", "running"),
                       trow("T-B", "Game Timer", "00:01:00", "stopped")]
    check("a started timer is announced", await until(lambda: any(
        t["state"] == "running" for m in frames(q) for t in m["timers"])))
    # One timer-second per real second, as ProPresenter counts: a faster fake would look like someone
    # changing the timer (TIMER_JUMP_S) on a runner that reads it less often, and that is a new message.
    for secs in range(298, 295, -1):
        fake.timer_rows[0] = trow("T-A", "Segment Countdown", f"00:0{secs // 60}:{secs % 60:02d}", "running")
        await asyncio.sleep(1.0)
    sent_while_counting = frames(q)
    eq("while it counts down, nothing more is sent (the panel counts itself)", sent_while_counting, [])

    # -- a timer changed in ProPresenter while running is a new message at once
    fake.timer_rows[0] = trow("T-A", "Segment Countdown", "00:09:00", "running")
    jumped = await until(lambda: any(m["timers"][0]["value"] > 500 for m in frames(q)))
    check("time added to a running timer is sent at once", jumped)

    # -- thresholds: the colour state is Relay's reading of the same seconds
    fake.timer_rows[0] = trow("T-A", "Segment Countdown", "00:01:30", "running")
    check("amber is reported under two minutes", await until(lambda: any(
        m["timers"][0]["level"] == "amber" for m in frames(q))))
    fake.timer_rows[0] = trow("T-A", "Segment Countdown", "00:00:20", "running")
    check("red under half a minute", await until(lambda: any(m["timers"][0]["level"] == "red" for m in frames(q))))
    fake.timer_rows[0] = trow("T-A", "Segment Countdown", "-00:00:03", "overrunning")
    over = None

    def saw_over():
        nonlocal over
        for m in frames(q):
            if m["timers"][0]["level"] == "over":
                over = m["timers"][0]
        return over is not None

    check("past zero it is over, with a negative time", await until(saw_over))
    check("and the seconds are negative", over["value"] < 0 and over["state"] == "overrunning", over)
    fake.timer_rows[0] = trow("T-A", "Segment Countdown", "00:05:00", "stopped")
    check("a reset timer is ok again", await until(lambda: any(m["timers"][0]["state"] == "stopped" for m in frames(q))))

    # -- the heartbeat in main re-sends the state as it is
    hb = feed.payload("")
    eq("a heartbeat is the same state, with Relay's clock", (hb["type"], len(hb["timers"]), "server_time" in hb),
       ("pptimers", 2, True))

    # -- which timers: the global setting, then a panel's own
    config.save({"propresenter": {**config.get()["propresenter"], "timers": {"amber_s": 60, "red_s": 10, "show": ["T-B"]}}},
                replace=("propresenter",))
    feed.refresh()
    msg = frames(q)[-1]
    eq("the global setting picks the timers and the thresholds", ([t["id"] for t in msg["timers"]], msg["amber_s"], msg["red_s"]),
       (["T-B"], 60, 10))
    feed.panel_settings = lambda pid: {"amber_s": 300, "show": ["T-A", "T-B"]} if pid == "a" * 32 else None
    q2 = feed.subscribe("a" * 32)
    mine = frames(q2)[0]
    eq("a panel's own setting beats the global one, key by key", ([t["id"] for t in mine["timers"]], mine["amber_s"],
                                                                   mine["red_s"]), (["T-A", "T-B"], 300, 10))
    eq("another panel is unaffected", [t["id"] for t in feed.payload("b" * 32)["timers"]], ["T-B"])
    check("a heartbeat on the panel's own stream does not leak into the other's", not frames(q))
    feed.panel_settings = lambda pid: {"amber_s": 20, "red_s": 5, "show": ["T-A"]} if pid == "a" * 32 else None
    feed.refresh()
    again = frames(q2)
    eq("changing a panel's settings reaches its open stream, and only that one",
       ([(t["id"], m["amber_s"], m["red_s"]) for m in again for t in m["timers"]], frames(q)),
       ([("T-A", 20, 5)], []))
    # at most four timers
    fake.timer_rows = [trow(f"T-{i}", f"Timer {i}", "00:01:00", "stopped") for i in range(6)]
    config.save({"propresenter": {**config.get()["propresenter"], "timers": {"amber_s": 120, "red_s": 30, "show": []}}},
                replace=("propresenter",))
    check("six timers in ProPresenter, four sent", await until(lambda: len(feed.payload("")["timers"]) == 4))

    # -- ProPresenter away: not live, no rows, never the last values
    fake.timer_rows = [trow("T-A", "Segment Countdown", "00:05:00", "running")]
    fake.kill.set()
    f.cancel()
    f2 = pp.Follower()
    f2.start()
    f2.cancel()
    fake.kill.clear()
    dead = pp.TimersFeed(pp.Follower())
    p = dead.payload("")
    eq("with ProPresenter not answering the feed says not live and sends no timers", (p["enabled"], p["live"], p["timers"]),
       (True, False, []))
    config.save({"propresenter": {"enabled": False}}, replace=("propresenter",))
    off = dead.payload("")
    eq("switched off, the message carries nothing else", (off["enabled"], off["live"], off["timers"]), (False, False, []))
    f.cancel()


asyncio.run(scenario_panel_feeds())

print("panel feeds: the routes")
configure(srv)
r = viewer.get("/api/targets")
eq("/api/targets offers lyrics (switched on for panels) and the timers", r.json()["propresenter"],
   {"lyrics": True, "timers": True})
admin.post("/api/admin/propresenter", json={"lyrics": {"panels": False}})
eq("…and not the lyrics once they are off for panels", viewer.get("/api/targets").json()["propresenter"],
   {"lyrics": False, "timers": True})
admin.post("/api/admin/propresenter", json={"lyrics": {"panels": True}})
config.save({"propresenter": {"enabled": False}}, replace=("propresenter",))
eq("…and neither while ProPresenter is off", viewer.get("/api/targets").json()["propresenter"],
   {"lyrics": False, "timers": False})
eq("the caption stream's own targets message carries it too", "propresenter" in app_main.engine.targets_payload(), True)
eq("the timers stream is refused while ProPresenter is off", viewer.get("/stream?stream=pptimers&for=panel").status_code, 403)
configure(srv)
eq("an audience other than the panel is refused", viewer.get("/stream?stream=pptimers&for=tv").status_code, 400)
eq("a panel id that is not one is refused", viewer.get("/stream?stream=pptimers&for=panel&panel=../x").status_code, 400)


async def read_first_frame(response):
    it = response.body_iterator
    chunk = await asyncio.wait_for(it.__anext__(), 3)
    await it.aclose()   # the client going away: the generator's finally unsubscribes
    return json.loads((chunk if isinstance(chunk, str) else chunk.decode())[len("data: "):])


async def scenario_timers_route():
    fake.timer_rows = [trow("T-A", "Segment Countdown", "00:05:00", "stopped")]
    fake.timer_cfg = [cfg_row("T-A", "Segment Countdown", "down")]
    configure(srv)
    pp.follower.start()
    check("the app's follower is up", await until(lambda: pp.follower.up and pp.follower.timers()))
    pid = "c" * 32
    got = await read_first_frame(await app_main.stream(
        type("R", (), {"query_params": {"for": "panel", "panel": pid}})(), stream="pptimers"))
    eq("the route's first message is the timers, whole", (got["type"], got["enabled"], got["live"],
                                                           [t["name"] for t in got["timers"]]),
       ("pptimers", True, True, ["Segment Countdown"]))
    check("a reader that has gone is not kept", pid not in pp.timers_feed.subs and "" not in pp.timers_feed.subs,
          pp.timers_feed.subs)
    pp.follower.cancel()


asyncio.run(scenario_timers_route())

# ------------------------------------------------------------------------------------------- slide-note tags
print("slide-note tags")
from app.modules.propresenter import slidetags as st  # noqa: E402


async def scenario_tags():
    fired = []

    def rec(name):
        async def fn(value):
            fired.append((name, value))
            return "done"
        return fn
    real_actions = dict(st.tags.actions)
    st.tags.actions = {n: rec(n) for n in st.ACTIONS}
    st.tags.reset()
    st.tags.history.clear()
    st.tags.decks.clear()
    st.tags.deck_reads = 0
    try:
        fake.requests.clear()
        fake.notes = {0: "Song starts here [relay-deck:lyrics]", 2: "Sermon [relay:capture-start] [relay:oops]"}
        fake.song("Tagged Song", groups=[("Verse 1", ["one", "two"]), ("Chorus", ["three", "four"])])
        fake.index = 0
        configure(srv)                                  # tags default: off
        f = pp.Follower()
        f.start()
        check("tags off: the follower is up and reads the slide",
              await until(lambda: f.up and f.lyrics()["text"] == "one"))
        await asyncio.sleep(0.6)
        eq("tags off: nothing fires", fired, [])
        eq("tags off: nothing is shown as seen", st.tags.seen, None)

        configure(srv, tags={"enabled": True, "settle_s": 0.4})
        check("tags on: the first slide's deck tag is read from the presentation's notes",
              await until(lambda: st.tags.seen and st.tags.seen["deck_tags"] == ["lyrics"], 4), st.tags.seen)
        eq("the deck default shows as what the slide resolves to", st.tags.seen["resolved"], ["lyrics"])
        eq("it is waiting out the settle time", (fired, st.tags.seen["settling"]), ([], True))
        check("then it fires, once", await until(lambda: fired == [("lyrics", None)]), fired)
        reads = [r for r in fake.requests if r[1].startswith("/v1/presentation/P-Tagg")]
        eq("the whole presentation was read once for the deck-wide tags", len(reads), 1)

        for i in (1, 0, 1):
            fake.index = i
            await asyncio.sleep(0.15)
        await asyncio.sleep(0.9)
        eq("moving between slides with the deck tag fires nothing more", fired, [("lyrics", None)])
        reads = [r for r in fake.requests if r[1].startswith("/v1/presentation/P-Tagg")]
        eq("…and the presentation was not read again for each slide", len(reads), 1)

        fake.index = 2
        check("a slide with its own tag runs that and not the deck's",
              await until(lambda: fired[-1:] == [("capture-start", None)], 4), fired)
        check("an unknown action is a warning, not an action",
              any("Unknown action" in w for w in st.tags.seen["warnings"]), st.tags.seen["warnings"])
        eq("the slide's tags and what it resolved to are shown",
           (st.tags.seen["slide_tags"], st.tags.seen["resolved"], st.tags.seen["slide"]),
           (["capture-start"], ["capture-start"], 3))
        fake.index = 3
        check("a slide with none falls back to the deck's: the set changed, so it runs",
              await until(lambda: fired[-1:] == [("lyrics", None)], 4), fired)
        eq("…in all, in this order", fired, [("lyrics", None), ("capture-start", None), ("lyrics", None)])
        eq("what fired is listed for admin, newest first",
           [(r["action"], r["ok"]) for r in st.tags.history][:2], [("lyrics", True), ("capture-start", True)])

        # a fast run
        fired.clear()
        fake.notes = {i: f"[relay:alert=Slide {i}]" for i in range(4)}
        for i in (0, 1, 2, 3, 0, 1, 2):
            fake.index = i
            await asyncio.sleep(0.12)
        await until(lambda: fired, 3)
        await asyncio.sleep(0.5)
        eq("a fast run of slides fires once, for the slide it stopped on", fired, [("alert", "Slide 2")])

        # read-only toward ProPresenter, view-only does not stop Relay-side actions, ProPresenter's capture is never called
        writes = [r for r in fake.requests if r[0] != "GET"]
        eq("tags never write to ProPresenter (no notes edited, nothing triggered)", writes, [])
        check("ProPresenter's capture is never called",
              not any("/capture" in r[1] for r in fake.requests), [r[1] for r in fake.requests if "/capture" in r[1]])
        fired.clear()
        configure(srv, view_only=True, tags={"enabled": True, "settle_s": 0.4})
        fake.notes = {0: "[relay:captions]", 1: "[relay:captions]", 2: "[relay:captions]", 3: "[relay:lang=fr]"}
        fake.index = 3
        check("view-only does not stop an action that stays inside Relay",
              await until(lambda: fired == [("lang", "FRENCH")], 4), fired)

        # off again
        fired.clear()
        configure(srv, tags={"enabled": False, "settle_s": 0.4})
        fake.index = 0
        await asyncio.sleep(1.0)
        eq("switched off: nothing fires", fired, [])
        eq("…and nothing is shown", st.tags.seen, None)

        # the admin page
        configure(srv, tags={"enabled": True, "settle_s": 0.4})
        fake.index = 2
        pp.follower.start()
        check("the admin body carries the tags' state",
              await until(lambda: admin.get("/api/admin/propresenter").json()["tags"]["seen"] is not None, 5))
        body = admin.get("/api/admin/propresenter").json()
        eq("…its shape", sorted(body["tags"]), ["actions", "fired", "seen"])
        pp.follower.cancel()
        f.cancel()
    finally:
        st.tags.actions = real_actions
        st.tags.reset()
        configure(srv)


asyncio.run(scenario_tags())

# the settings route
r = admin.post("/api/admin/propresenter", json={"tags": {"enabled": True, "settle_s": 2.5}})
eq("the tag settings are saved", (r.status_code, r.json()["settings"]["tags"]), (200, {"enabled": True, "settle_s": 2.5}))
r = admin.post("/api/admin/propresenter", json={"tags": {"settle_s": 99}})
eq("a settle time out of range is refused with a fixed sentence", (r.status_code, r.json()["error"]),
   (400, "The settle time is a number of seconds from 0.2 to 30."))
eq("…and changed nothing", config.get()["propresenter"]["tags"], {"enabled": True, "settle_s": 2.5})
r = admin.post("/api/admin/propresenter", json={"host": "127.0.0.1"})
eq("saving something else leaves the tag settings alone", r.json()["settings"]["tags"], {"enabled": True, "settle_s": 2.5})
r = admin.post("/api/admin/propresenter", json={"tags": {"enabled": False}})
eq("tags can be switched off without touching the settle time", r.json()["settings"]["tags"],
   {"enabled": False, "settle_s": 2.5})
eq("the tag state is not for anyone without signing in",
   TestClient(app, base_url="http://relay.local:8001").get("/api/admin/propresenter").status_code, 401)

# the admin route: global timer settings
r = admin.post("/api/admin/propresenter", json={"timers": {"amber_s": 150, "red_s": 45, "show": ["T-A"]}})
eq("the global timer settings are saved", (r.status_code, r.json()["settings"]["timers"]),
   (200, {"amber_s": 150, "red_s": 45, "show": ["T-A"]}))
r = admin.post("/api/admin/propresenter", json={"timers": {"amber_s": 10, "red_s": 40}})
eq("red later than amber is held to amber", r.json()["settings"]["timers"]["red_s"], 10)
r = admin.post("/api/admin/propresenter", json={"timers": {"amber_s": "soon"}})
eq("a bad threshold is refused with a fixed sentence", (r.status_code, r.json()["error"]),
   (400, "The amber threshold is a number of seconds from 0 to 86399."))
eq("…and changed nothing", config.get()["propresenter"]["timers"]["amber_s"], 10)
r = admin.post("/api/admin/propresenter", json={"timers": {"show": "everything"}})
eq("a bad list of timers is refused", (r.status_code, r.json()["error"]), (400, "Timers to show is a list of timer IDs."))
anon_pp = TestClient(app, base_url="http://relay.local:8001")
eq("the setting is not for anyone without signing in", anon_pp.post("/api/admin/propresenter",
                                                                       json={"timers": {"amber_s": 1}}).status_code, 401)

# ------------------------------------------------------------------------------------------------ discovery
print("discovery")


def rr(name, rtype, rdata, ttl=120):
    return pp._name(name.split(".")) + struct.pack("!HHIH", rtype, 1, ttl, len(rdata)) + rdata


inst = "Studio-Mac._proapiv1ws._tcp.local"
pkt = struct.pack("!6H", 0, 0x8400, 0, 1, 0, 2)
pkt += rr("_proapiv1ws._tcp.local", 12, pp._name(inst.split(".")))
pkt += rr(inst, 33, struct.pack("!HHH", 0, 0, 50402) + pp._name(["Studio-Mac", "local"]))
pkt += rr("Studio-Mac.local", 1, socket.inet_aton("10.0.0.20"))
found = pp.collect(pp.parse_mdns(pkt))
eq("an mDNS answer becomes a name, host, address and the API port",
   found, [{"name": "Studio-Mac", "host": "Studio-Mac.local", "address": "10.0.0.20", "port": 50402}])
eq("a record that is not ours is ignored", pp.collect(pp.parse_mdns(
    struct.pack("!6H", 0, 0x8400, 0, 1, 0, 0) + rr("_other._tcp.local", 12, pp._name(["x", "_other", "_tcp", "local"])))), [])
try:
    pp.parse_mdns(b"\x00\x01")
    check("a short packet is refused", False)
except ValueError:
    check("a short packet is refused", True)
eq("the follower takes the name given, not the Bonjour lookup, when it is an address", pp.resolve("127.0.0.1", 1),
   "127.0.0.1")
eq("a name is looked up once and the answer is an address", pp.resolve("localhost", 1) in ("127.0.0.1", "::1"), True)

srv.stop()
print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
