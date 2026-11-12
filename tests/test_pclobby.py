"""Lobby announcements and the slide view (#138 stage 7): the rotation's timing, pausing and resuming when the
operator puts something else up, stopping, view-only refusal; the slide images' proxy and cache; the overlay's
timer and media-progress arithmetic; and who may see what.

Everything is a fake: ProPresenter is a Client whose `_send` answers from tables and moves a small "world" (what is on
the announcements layer) when a trigger arrives, so view-only and every write rule of the real Client still apply.
Nothing touches a network.

Offline. Run: python tests/test_pclobby.py
"""
import asyncio
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pg  # noqa: E402  (a throwaway Postgres for the user system; must come before app.main)

_pg.start()
_pg.reset()

from fastapi.testclient import TestClient

import app.main as main
from app import config
from app.modules.propresenter import ppview, propresenter as pp
from app.main import app
from app.modules.propresenter.pplobby import lobby as pp_lobby
from app.modules.propresenter.ppview import view as pp_view
from app.modules.propresenter.ppcontrol import control as ctl
from app.modules.propresenter.pplobby import Lobby, ROTATE_TYPES, GRACE_S, RESUME_S

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label + (f" ({extra})" if extra and not cond else ""), bool(cond), True)


PRES = {"P-A": ("Welcome", ["a1", "a2", "a3"]), "P-B": ("Coffee", ["b1"]), "P-X": ("Sermon", ["x1", "x2"])}


class FakePP(pp.Client):
    """ProPresenter, answering from tables. A trigger moves `world` (the announcements layer); every write is
    recorded. The real Client still applies view-only."""

    def __init__(self):
        self.vo = False
        super().__init__("127.0.0.1", 1, lambda: self.vo)
        self.sent = []
        self.fail = None
        self.images = 0
        self.last_image_path = ""
        self.world = {"on": False, "uuid": "", "cue": None}
        self.items = [self.item(0, "Lobby heading", "header", ""), self.item(1, "Welcome", "presentation", "P-A"),
                      self.item(2, "Loop video", "media", ""), self.item(3, "Coffee", "presentation", "P-B"),
                      self.item(4, "Audio bed", "audio", ""), self.item(5, "Hidden", "presentation", "P-X", hidden=True)]

    @staticmethod
    def item(i, name, kind, uuid, hidden=False):
        o = {"id": {"uuid": f"I{i}", "name": name, "index": i}, "type": kind, "is_hidden": hidden, "is_pco": False}
        if kind == "presentation":
            o["presentation_info"] = {"presentation_uuid": uuid}
        return o

    def _send(self, method, path, body=None, *, timeout=3, raw_body=False):
        self.sent.append((method, path, body))
        if self.fail:
            raise pp.ProError(self.fail, "boom with a secret detail")
        if method == "GET" and path.startswith("/v1/playlist/PL-LOBBY/") and path.endswith("/trigger"):
            i = int(path.split("/")[4])
            it = self.items[i]
            uuid = (it.get("presentation_info") or {}).get("presentation_uuid", "")
            self.world = {"on": True, "uuid": uuid, "cue": 0 if uuid else None}
            return None
        if method == "GET" and path.startswith("/v1/announcement/active/") and path.endswith("/trigger"):
            self.world["cue"] = int(path.split("/")[4])
            return None
        if method == "GET" and path == "/v1/clear/layer/announcements":
            self.world = {"on": False, "uuid": "", "cue": None}
            return None
        if method == "GET" and path == "/v1/playlist/PL-LOBBY":
            return {"id": {"uuid": "PL-LOBBY", "name": "Lobby", "index": 0}, "items": self.items}
        if method == "GET" and path.startswith("/v1/presentation/") and "/thumbnail/" in path:
            self.images += 1
            self.last_image_path = path
            return ("image/jpeg", b"\xff\xd8JPEG")
        raise AssertionError(f"unexpected {method} {path}")

    def writes(self):
        return [s[1] for s in self.sent if s[0] != "GET" or s[1].endswith("/trigger") or "/clear/" in s[1]]

    def triggers(self):
        return [s[1] for s in self.sent if s[1].endswith("/trigger") or "/clear/" in s[1]]


class Quiet(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


logs = Quiet()
logging.getLogger().addHandler(logs)
logging.getLogger().setLevel(logging.INFO)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"propresenter": {"enabled": True}, "planning_center": {"enabled": True}}})
# Both modules are switched on (#157); their hooks into core are set without starting their background work.
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")
_modules.attach("propresenter")


def save_pp(**kw):
    cur = config.get()["propresenter"]
    config.save({"propresenter": pp.clean_stored({**cur, **kw})}, replace=("propresenter",))


save_pp(enabled=True, host="127.0.0.1", port=1)
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = _pg.client(app, base_url="http://relay.local:8001")
anon = TestClient(app, base_url="http://relay.local:8001")

fpp = FakePP()
f = pp.follower


async def _no_poke():
    pass


f.poke = _no_poke
f.client = fpp
f.up = True

T = [1000.0]
lobby = pp_lobby
lobby.clock = lambda: T[0]


def sync():
    """What the follower reads once a second: the layer, and the presentation on it."""
    w = fpp.world
    f.data["status/layers"] = {"announcements": w["on"], "slide": False, "media": False}
    if w["uuid"]:
        name, cues = PRES[w["uuid"]]
        f.announcement = {"uuid": w["uuid"], "name": name, "cues": [{"text": t} for t in cues]}
        f.announcement_index = w["cue"]
    else:
        f.announcement, f.announcement_index = None, None


def reset(**lob):
    fpp.sent.clear()
    fpp.vo, fpp.fail = False, None
    fpp.world = {"on": False, "uuid": "", "cue": None}
    f.up, f.client = True, fpp
    save_pp(view_only=False, lobby={"playlist": "PL-LOBBY", "interval_s": 8, "loop": True, **lob})
    lobby.running, lobby.paused, lobby.finished, lobby.error = False, "", False, ""
    sync()


async def settle(seconds, step=1.0, observe=True):
    end = T[0] + seconds
    while T[0] < end:
        T[0] += step
        if observe:
            sync()
        await lobby.tick()


async def attempt(coro):
    """('ok', result) or (the exception's class name, its message)."""
    try:
        return "ok", await coro
    except Exception as exc:
        return type(exc).__name__, str(exc)


def trig(path_tail):
    return f"/v1/playlist/PL-LOBBY/{path_tail}/trigger"


CUE = lambda n: f"/v1/announcement/active/{n}/trigger"

# ------------------------------------------------------------------------------------------------------ settings
print("settings")
d = pp.default_lobby()
eq("defaults: no playlist, 8 s, loop", (d["playlist"], d["interval_s"], d["loop"]), ("", 8.0, True))
eq("the view defaults to no timer and not public", pp.default_view(), {"timer": "", "public": False})
eq("a rotation is not a setting: nothing says running", "running" in d, False)
eq("a bad time is refused with a fixed sentence", pp.lobby_fields({"interval_s": 1})[1],
   "The time per slide is a number of seconds from 3 to 600.")
eq("and a string time too", pp.lobby_fields({"interval_s": "8"})[0], {})
eq("a path-shaped playlist is refused", pp.lobby_fields({"playlist": "../x"})[1], "That is not a ProPresenter playlist.")
eq("a path-shaped timer is refused", pp.view_fields({"timer": "a/b"})[1], "That is not a ProPresenter timer.")
eq("a string for loop is not on", pp.lobby_fields({"loop": "yes"})[0], {"loop": False})
eq("public needs a real true", pp.view_fields({"public": "true"})[0], {"public": False})
check("the saved config carries both blocks", all(k in config.get()["propresenter"] for k in ("lobby", "view")))
r = admin.post("/api/admin/propresenter", json={"lobby": {"interval_s": 0}})
eq("the save route answers a bad time with 400 and the sentence", (r.status_code, r.json()["error"]),
   (400, "The time per slide is a number of seconds from 3 to 600."))
r = admin.post("/api/admin/propresenter", json={"lobby": {"playlist": "PL-LOBBY", "interval_s": 12, "loop": False}})
eq("a good save", (r.status_code, config.get()["propresenter"]["lobby"]),
   (200, {"playlist": "PL-LOBBY", "interval_s": 12.0, "loop": False}))
r = admin.post("/api/admin/propresenter", json={"view": {"timer": "T-1"}})
eq("a view save keeps public off", (r.status_code, config.get()["propresenter"]["view"]), (200, {"timer": "T-1", "public": False}))
check("the answer carries the lobby state", "lobby" in r.json() and r.json()["lobby"]["running"] is False)
save_pp(view={"timer": "", "public": False})

# ------------------------------------------------------------------------------------------------------ access
print("who may reach what")
for method, path, body in [("get", "/api/admin/propresenter/lobby", None), ("post", "/api/admin/propresenter/lobby/start", {}),
                           ("post", "/api/admin/propresenter/lobby/resume", {}),
                           ("post", "/api/admin/propresenter/lobby/stop", {"clear": True}),
                           ("get", "/api/admin/propresenter/live", None),
                           ("get", "/api/admin/propresenter/live/image/current?v=x", None)]:
    kw = {"json": body} if body is not None else {}
    eq(f"{method.upper()} {path} needs a sign-in", getattr(anon, method)(path, **kw).status_code, 401)
    eq(f"{path} is not on the viewer port", getattr(viewer, method)(path, **kw).status_code, 404)
fpp.sent.clear()
for path in ("/stage", "/api/stage/live", "/api/stage/live/image/current?v=x", "/api/stage/live/image/next?v=x"):
    eq(f"{path} is a 404 while the public switch is off (the default)", viewer.get(path).status_code, 404)
eq("and nothing was asked of ProPresenter", fpp.sent, [])
eq("an unknown image name is refused even for admin", admin.get("/api/admin/propresenter/live/image/other?v=x").status_code, 404)

# ------------------------------------------------------------------------------------------------------ rotation
async def rotation():
    print("starting")
    reset(playlist="")
    eq("no playlist picked is refused with a fixed sentence", await attempt(lobby.start_rotation()),
       ("LobbyError", "Pick the announcement playlist first."))
    reset()
    f.up = False
    eq("ProPresenter not connected is refused", await attempt(lobby.start_rotation()),
       ("LobbyError", "ProPresenter is not connected."))
    reset()
    eq("nothing was written by either refusal", fpp.writes(), [])

    reset()
    T[0] = 1000.0
    state = await lobby.start_rotation()
    eq("start puts up the first presentation item (not the header)", fpp.triggers(), [trig(1)])
    eq("it shows headers, hidden items and audio are left out", [i["name"] for i in lobby.items], ["Welcome", "Loop video", "Coffee"])
    eq("state says running on item 1", (state["running"], state["item"], state["item_number"], state["item_count"], state["cue"]),
       (True, "Welcome", 1, 3, 1))
    check("types rotated are presentations and media only", ROTATE_TYPES == ("presentation", "media"))

    print("timing: a slide every interval, cues first, then the next item, then round again")
    fpp.sent.clear()
    await settle(7)
    eq("nothing before the interval has passed", fpp.triggers(), [])
    await settle(1)
    eq("at 8 s the next cue of the same presentation", fpp.triggers(), [CUE(1)])
    await settle(8)
    eq("at 16 s the third cue", fpp.triggers()[-1], CUE(2))
    await settle(8)
    eq("at 24 s the next playlist item (the media)", fpp.triggers()[-1], trig(2))
    await settle(8)
    eq("at 32 s the one after (Coffee)", fpp.triggers()[-1], trig(3))
    await settle(8)
    eq("at 40 s it starts over (loop is on)", fpp.triggers()[-1], trig(1))
    eq("six triggers, none extra", len(fpp.triggers()), 5)
    eq("running the whole time", lobby.running, True)
    await settle(60)
    eq("a minute later the same rhythm, still running and unpaused", (lobby.running, lobby.paused), (True, ""))

    print("an interval change applies at once; the playlist is re-read at the wrap")
    reset(interval_s=20)
    T[0] = 5000.0
    await lobby.start_rotation()
    fpp.sent.clear()
    await settle(19)
    eq("20 s interval: nothing at 19 s", fpp.triggers(), [])
    await settle(1)
    eq("and the step at 20 s", fpp.triggers(), [CUE(1)])
    fpp.items.append(FakePP.item(6, "New one", "presentation", "P-X"))
    await settle(20 * 4)     # A cue 2, media, B, then the wrap re-reads the playlist
    check("an item added to the playlist is picked up at the wrap", any(i["name"] == "New one" for i in lobby.items))
    fpp.items.pop()
    lobby.running = False

    print("no loop: it ends on the last slide and stays there")
    reset(loop=False, interval_s=3)
    T[0] = 6000.0
    await lobby.start_rotation()
    await settle(3 * 8)
    eq("finished, not running, no more writes after the last item", (lobby.running, lobby.finished), (False, True))
    eq("the last slide was left up", (fpp.world["on"], fpp.world["uuid"]), (True, "P-B"))
    n = len(fpp.triggers())
    await settle(30)
    eq("nothing is written once finished", len(fpp.triggers()), n)

    print("the operator puts something else up: pause, write nothing, carry on when it is gone")
    reset()
    T[0] = 7000.0
    await lobby.start_rotation()
    await settle(2)
    fpp.world = {"on": True, "uuid": "P-X", "cue": 0}        # a presentation that is not in the playlist
    await settle(GRACE_S + 3)
    eq("paused for the operator", (lobby.running, lobby.paused), (True, "operator"))
    fpp.sent.clear()
    await settle(40)
    eq("40 s later it has written nothing", fpp.triggers(), [])
    check("the state says why", "something else" in lobby_state_text())
    fpp.world = {"on": False, "uuid": "", "cue": None}       # the operator cleared theirs
    await settle(RESUME_S - 2)
    eq("not at once: the layer must stay empty a few seconds", fpp.triggers(), [])
    await settle(4)
    eq("then it starts again from the first item", fpp.triggers(), [trig(1)])
    eq("unpaused", lobby.paused, "")

    print("the operator puts one of the playlist's own slides up: no pause, the rotation carries on from it")
    reset()
    T[0] = 8000.0
    await lobby.start_rotation()
    await settle(2)
    fpp.world = {"on": True, "uuid": "P-A", "cue": 2}        # nudged to the last cue of the same presentation
    await settle(GRACE_S + 1)
    eq("not paused", lobby.paused, "")
    fpp.sent.clear()
    await settle(10)
    eq("it moved on from where the operator left it (the next item after cue 3)", fpp.triggers(), [trig(2)])

    print("the operator comes back to the playlist after a pause: carried on, no restart")
    reset()
    T[0] = 9000.0
    await lobby.start_rotation()
    await settle(2)
    fpp.world = {"on": True, "uuid": "P-X", "cue": 0}
    await settle(GRACE_S + 2)
    eq("paused", lobby.paused, "operator")
    fpp.world = {"on": True, "uuid": "P-B", "cue": 0}
    fpp.sent.clear()
    await settle(2)
    eq("resumed from where it is, without a write", (lobby.paused, fpp.triggers()), ("", []))

    print("the operator clears the layer: paused until Resume or Stop")
    reset()
    T[0] = 10000.0
    await lobby.start_rotation()
    await settle(2)
    fpp.world = {"on": False, "uuid": "", "cue": None}
    await settle(GRACE_S + 2)
    eq("paused because it was cleared", (lobby.running, lobby.paused), (True, "cleared"))
    fpp.sent.clear()
    await settle(60)
    eq("a deliberate clear is not undone", fpp.triggers(), [])
    st = await lobby.resume()
    eq("Resume starts again from the top", fpp.triggers(), [trig(1)])
    eq("and the state is running unpaused", (st["running"], st["paused"]), (True, ""))

    print("it survives ProPresenter dropping off and coming back")
    reset()
    T[0] = 11000.0
    await lobby.start_rotation()
    await settle(2)
    f.up = False
    fpp.sent.clear()
    await settle(30, observe=False)
    eq("still running, waiting, and nothing written while it is away", (lobby.running, fpp.triggers(), lobby.state()["waiting"]),
       (True, [], True))
    f.up = True
    sync()
    await settle(1)
    eq("back with the layer as it was: carries on (the overdue step), does not restart the playlist",
       (fpp.triggers(), lobby.paused), ([CUE(1)], ""))
    reset()
    T[0] = 12000.0
    await lobby.start_rotation()
    await settle(2)
    f.up = False
    await settle(10, observe=False)
    fpp.world = {"on": False, "uuid": "", "cue": None}          # ProPresenter was restarted: the layer is empty
    f.up = True
    fpp.sent.clear()
    await settle(2)
    eq("back with an empty layer: it starts the playlist over, not paused", (fpp.triggers()[:1], lobby.paused), ([trig(1)], ""))

    print("view-only")
    reset()
    fpp.vo = True
    res = await attempt(lobby.start_rotation())
    eq("start is refused as view-only", (res[0], "View-only mode" in res[1]), ("ViewOnly", True))
    eq("and nothing was written", fpp.writes(), [])
    eq("and no rotation was started", lobby.running, False)
    fpp.vo = False
    T[0] = 13000.0
    await lobby.start_rotation()
    fpp.vo = True
    fpp.sent.clear()
    await settle(10)
    eq("view-only switched on mid-run ends it, with the reason", (lobby.running, "View-only" in lobby.state()["error"]), (False, True))
    eq("with no write", fpp.writes(), [])

    print("stop, shutdown and switching ProPresenter off")
    reset()
    T[0] = 14000.0
    await lobby.start_rotation()
    fpp.sent.clear()
    st = await lobby.stop_rotation()
    eq("stop stops and writes nothing", (st["running"], fpp.writes()), (False, []))
    await settle(30)
    eq("and stays stopped", (lobby.running, fpp.writes()), (False, []))
    await lobby.start_rotation()
    fpp.sent.clear()
    st = await lobby.stop_rotation(True)
    eq("stop and clear also clears announcements", (st["running"], fpp.triggers()), (False, ["/v1/clear/layer/announcements"]))
    await lobby.start_rotation()
    fpp.vo = True
    fpp.sent.clear()
    res = await attempt(lobby.stop_rotation(True))
    eq("view-only refuses the clear but the rotation is stopped", (res[0], lobby.running), ("ViewOnly", False))
    fpp.vo = False
    await lobby.start_rotation()
    save_pp(enabled=False)
    fpp.sent.clear()
    await settle(30)
    eq("switching ProPresenter off in admin stops it, with no write", (lobby.running, fpp.writes()), (False, []))
    save_pp(enabled=True)
    reset()
    await lobby.start_rotation()
    save_pp(lobby={"playlist": "PL-OTHER"})
    fpp.sent.clear()
    await settle(3)
    eq("picking another playlist ends the rotation", lobby.running, False)
    reset()
    await lobby.start_rotation()
    fpp.sent.clear()
    lobby.start()
    lobby.cancel()
    await asyncio.sleep(0)
    await settle(30)
    eq("shutdown (cancel) stops it and writes nothing", (lobby.running, lobby._task, fpp.writes()), (False, None, []))

    print("a failing step is retried, not skipped, and says so without exception text")
    reset()
    T[0] = 15000.0
    await lobby.start_rotation()
    await settle(7)
    fpp.fail = "timeout"
    fpp.sent.clear()
    await settle(5)
    eq("still running, with a fixed sentence", (lobby.running, lobby.state()["error"]),
       (True, "ProPresenter did not take the last step; Relay will try again."))
    check("the secret detail stays in the log only", all("boom" not in str(v) for v in lobby.state().values()))
    fpp.fail = None
    fpp.sent.clear()
    await settle(2)
    eq("the same step happens when it recovers", fpp.triggers(), [CUE(1)])
    eq("and the error clears", lobby.state()["error"], "")


def lobby_state_text():
    return {"operator": "something else"}.get(lobby.paused, "")


asyncio.run(rotation())

print("the routes answer refusals with fixed sentences")
reset(playlist="")
r = admin.post("/api/admin/propresenter/lobby/start")
eq("no playlist: 409 and the sentence", (r.status_code, r.json()["error"]), (409, "Pick the announcement playlist first."))
reset()
f.up = False
eq("not connected: 409", admin.post("/api/admin/propresenter/lobby/start").status_code, 409)
reset()
fpp.vo = True
r = admin.post("/api/admin/propresenter/lobby/start")
eq("view-only: 409 and the sentence, nothing written", (r.status_code, "View-only mode" in r.json()["error"], fpp.writes()), (409, True, []))
fpp.vo = False
fpp.fail = "http"
r = admin.post("/api/admin/propresenter/lobby/start")
check("a failing ProPresenter is a fixed sentence with no exception text", r.status_code in (502, 504) and "boom" not in r.text and "secret" not in r.text)
fpp.fail = None
reset()
r = admin.post("/api/admin/propresenter/lobby/start")
eq("a good start through the route", (r.status_code, r.json()["running"], fpp.triggers()), (200, True, [trig(1)]))
r = admin.post("/api/admin/propresenter/lobby/stop", json={"clear": True})
eq("and a stop with clear", (r.status_code, r.json()["running"], fpp.triggers()[-1]), (200, False, "/v1/clear/layer/announcements"))
fpp.vo = True
r = admin.post("/api/admin/propresenter/lobby/stop", json={"clear": True})
eq("view-only stop and clear is a 409", r.status_code, 409)
fpp.vo = False

# ------------------------------------------------------------------------------------------------------ the view
print("the slide view: overlay arithmetic")
NOW = [5000.0]
ppview.time = types.SimpleNamespace(time=lambda: NOW[0])
f.up = True
f.presentation = {"uuid": "P-A", "name": "Welcome", "path": "", "library": "Default", "groups": [],
                  "cues": [{"text": "a1", "group": "Verse 1", "color": "#ff0000", "label": ""}, {"text": "a2", "group": "Chorus"},
                           {"text": "a3", "group": "Chorus"}]}
f.slide_index = 0
f.current, f.next = {"text": "a1", "notes": ""}, {"text": "a2", "notes": ""}
f.timer_kinds = {"T-DOWN": "down", "T-UP": "up"}
f.data["timers/current"] = [{"id": {"uuid": "T-DOWN", "name": "Segment"}, "time": "00:02:10", "state": "running"},
                            {"id": {"uuid": "T-UP", "name": "Elapsed"}, "time": "00:00:30", "state": "running"},
                            {"id": {"uuid": "T-STOP", "name": "Stopped"}, "time": "00:01:00", "state": "stopped"},
                            {"id": {"uuid": "T-OVER", "name": "Over"}, "time": "-00:00:05", "state": "overrunning"}]
f.timers_read_at = NOW[0] - 3
f.media = None
save_pp(view={"timer": "T-DOWN", "public": False})
snap = pp_view.snapshot("/api/admin/propresenter/live")
eq("live with the title and part", (snap["live"], snap["title"], snap["part"], snap["part_color"], snap["slide_number"], snap["slide_count"]),
   (True, "Welcome", "Verse 1", "#ff0000", 1, 3))
eq("a running countdown read 3 s ago is 3 s lower", (snap["timer"]["value"], snap["timer"]["dir"], snap["timer"]["state"]),
   (127.0, "down", "running"))
eq("amber/red follow the timers' thresholds (120 / 30): 127 is ok", snap["timer"]["level"], "ok")
f.timers_read_at = NOW[0] - 10
eq("a reading older than 5 s is carried only 5 s", pp_view.snapshot("x")["timer"]["value"], 125.0)
f.timers_read_at = NOW[0] - 8
f.data["timers/current"][0]["time"] = "00:02:00"
eq("under the amber threshold", pp_view.snapshot("x")["timer"]["level"], "amber")
f.data["timers/current"][0]["time"] = "00:00:20"
eq("under the red threshold", pp_view.snapshot("x")["timer"]["level"], "red")
save_pp(view={"timer": "T-UP", "public": False})
f.timers_read_at = NOW[0] - 4
eq("an elapsed timer counts up", pp_view.snapshot("x")["timer"]["value"], 34.0)
save_pp(view={"timer": "T-STOP", "public": False})
eq("a stopped timer does not move", pp_view.snapshot("x")["timer"]["value"], 60.0)
save_pp(view={"timer": "T-OVER", "public": False})
eq("an overrunning countdown goes further negative", pp_view.snapshot("x")["timer"]["value"], -9.0)
save_pp(view={"timer": "T-GONE", "public": False})
eq("a chosen timer that is not there gives no overlay", pp_view.snapshot("x")["timer"], None)
save_pp(view={"timer": "", "public": False})
eq("no chosen timer gives no overlay", pp_view.snapshot("x")["timer"], None)

f.media = {"time": 10.0, "duration": 40.0, "playing": True, "name": "Intro video", "audio_only": False, "read_at": NOW[0] - 2}
m = pp_view.snapshot("x")["media"]
eq("a playing video is carried forward by the reading's age", (m["time"], m["remaining"], m["percent"], m["playing"]), (12.0, 28.0, 30.0, True))
f.media["playing"] = False
m = pp_view.snapshot("x")["media"]
eq("a paused one stays where it is", (m["time"], m["percent"], m["playing"]), (10.0, 25.0, False))
f.media.update(playing=True, time=39.0, read_at=NOW[0] - 4)
m = pp_view.snapshot("x")["media"]
eq("it cannot run past its end", (m["time"], m["percent"], m["remaining"]), (40.0, 100.0, 0.0))
f.media.update(duration=0.0)
eq("a still (no length) has no progress bar", pp_view.snapshot("x")["media"], None)
f.media = None
eq("no media, no bar", pp_view.snapshot("x")["media"], None)

print("the follower reads the media only while the slide or media layer has something")
async def media_reads():
    class Reader(FakePP):
        def _send(self, method, path, body=None, **kw):
            self.sent.append((method, path, body))
            if path == "/v1/transport/presentation/time":
                return 12.5
            if path == "/v1/transport/presentation/current":
                return {"is_playing": True, "uuid": "M", "name": "Clip  one", "artist": "", "audio_only": False, "duration": 90.0}
            raise AssertionError(path)
    rd = Reader()
    fl = pp.Follower()
    fl.up = True
    fl.data["status/layers"] = {"slide": False, "media": False}
    await fl._read_media(rd)
    eq("layers empty: nothing is asked", (rd.sent, fl.media), ([], None))
    fl.data["status/layers"] = {"slide": True, "media": False}
    await fl._read_media(rd)
    eq("slide layer live: both are read", [s[1] for s in rd.sent], ["/v1/transport/presentation/time", "/v1/transport/presentation/current"])
    eq("and shaped", (fl.media["time"], fl.media["duration"], fl.media["playing"], fl.media["name"]), (12.5, 90.0, True, "Clip one"))
    fl.data["status/layers"] = {"slide": False, "media": False}
    await fl._read_media(rd)
    eq("cleared again: the reading is dropped", fl.media, None)
    rd.sent.clear()
    fl.data["status/layers"] = {"media": True}
    rd2 = FakePP()
    rd2.fail = "timeout"
    await fl._read_media(rd2)
    eq("a failed read is no media, not a crash", fl.media, None)
asyncio.run(media_reads())

print("images: proxied, cached, bigger, and only for the slide that is up")
snap = pp_view.snapshot("/api/admin/propresenter/live")
check("the image URLs are Relay's own, under the route", snap["current"]["image"].startswith("/api/admin/propresenter/live/image/current?v="))
check("and none points at ProPresenter", "127.0.0.1" not in snap["current"]["image"] and "presentation/" not in snap["current"]["image"])
eq("the text is there as a fallback", (snap["current"]["text"], snap["next"]["text"]), ("a1", "a2"))
f.client = fpp
fpp.sent.clear()
r = admin.get(snap["current"]["image"])
eq("the image comes back with its type", (r.status_code, r.headers["content-type"], r.content), (200, "image/jpeg", b"\xff\xd8JPEG"))
check("it was asked at the large size, not the grid's", "quality=960" in fpp.last_image_path)
r = admin.get(snap["current"]["image"])
eq("the same slide again is served from the cache", (r.status_code, fpp.images), (200, 1))
r = admin.get(snap["next"]["image"])
eq("the next slide is its own picture", (r.status_code, fpp.images, "/thumbnail/1?" in fpp.last_image_path), (200, 2, True))
r = admin.get("/api/admin/propresenter/live/image/current?v=stale")
eq("a marker that is out of date is a 404 with a fixed sentence", (r.status_code, r.json()["error"]),
   (404, "That slide is not up any more."))
eq("and ProPresenter was not asked", fpp.images, 2)
f.slide_index = 1
snap2 = pp_view.snapshot("x")
check("the next slide moving changes the image marker", snap2["current"]["image"] != snap["current"]["image"])
f.slide_index = 2
eq("at the last slide there is no next picture", pp_view.snapshot("x")["next"]["image"], "")
eq("so the route has none to draw", admin.get("/api/admin/propresenter/live/image/next?v=x").status_code, 404)
f.slide_index = 0
f.presentation["cues"] = []
s3 = pp_view.snapshot("x")
eq("no cue list: text only, no image", (s3["current"]["image"], s3["current"]["text"]), ("", "a1"))
f.presentation["cues"] = [{"text": "a1"}, {"text": "a2"}, {"text": "a3"}]
fpp.fail = "timeout"
r = admin.get(pp_view.snapshot("/api/admin/propresenter/live")["current"]["image"].replace("a1", "a1"))
check("a ProPresenter that does not answer is a 504 with a fixed sentence and no detail",
      r.status_code in (200, 504) and "boom" not in r.text)
fpp.fail = None
f.up = False
eq("not connected: nothing live", pp_view.snapshot("x")["live"], False)
f.up = True
f.presentation = None
eq("no presentation: nothing live", pp_view.snapshot("x")["live"], False)
f.presentation = {"uuid": "P-A", "name": "Welcome", "path": "", "library": "Default", "groups": [], "cues": [{"text": "a1"}, {"text": "a2"}]}

print("the public switch")
save_pp(view={"timer": "", "public": True})
r = viewer.get("/api/stage/live")
eq("on: the viewer port gets the snapshot", (r.status_code, r.json()["live"]), (200, True))
check("its image URLs are under /api/stage", r.json()["current"]["image"].startswith("/api/stage/live/image/current?v="))
r2 = viewer.get(r.json()["current"]["image"])
eq("and the picture", (r2.status_code, r2.content), (200, b"\xff\xd8JPEG"))
eq("the page is served", (viewer.get("/stage").status_code, "slideview.js" in viewer.get("/stage").text), (200, True))
eq("it offers only the live and next slide, no arbitrary slide", viewer.get("/api/stage/live/image/2?v=x").status_code, 404)
check("admin routes stay off the viewer port", viewer.get("/api/admin/propresenter/live").status_code == 404)
save_pp(view={"timer": "", "public": False})
eq("off again: 404", viewer.get("/api/stage/live").status_code, 404)
save_pp(enabled=False)
save_pp(view={"timer": "", "public": True})
eq("public on but ProPresenter off: still 404", viewer.get("/api/stage/live").status_code, 404)
save_pp(enabled=True, view={"timer": "", "public": False})

print("the pages carry the new controls and nothing from the server is put in as markup")
from app.modules.propresenter import MODULE as _pp_module  # noqa: E402
page = (Path(__file__).resolve().parent.parent / "app/modules/propresenter/templates/live.html").read_text()
check("the admin page has the slide view and lobby section",
      all(k in page for k in ("lbStart", "svRoot")) and "pplive" in [p.id for p in _pp_module.admin_pages]
      and {"pplive.js", "slideview.js"} <= set(_pp_module.admin_scripts))
_static = Path(__file__).resolve().parent.parent / "app/modules/propresenter/static"
js = (_static / "pplive.js").read_text() + (_static / "slideview.js").read_text()
check("no innerHTML anywhere in the new scripts", "innerHTML" not in js and "insertAdjacentHTML" not in js)

check("no JSON answer or state carried the exception detail", "secret detail" not in str(lobby.state()))

print()
print("FAILED:" if fails else "all passed", *fails, sep="\n  " if fails else " ")
sys.exit(1 if fails else 0)
