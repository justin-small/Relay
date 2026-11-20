"""Scheduled YouTube streams and their links in Planning Center Publishing (#411), against a fake YouTube and a
fake Planning Center: settings, when services are, matching a broadcast to an episode, creating and binding
broadcasts once, sending links once and on Send, refusals, the routes and their permissions, and the Planning Center
client's Publishing calls (on a local fake server). No network.

Run: .venv/bin/python tests/test_youtube_schedule.py
"""
import asyncio
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["TZ"] = "America/New_York"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import config, oauth, timewin  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.planning_center import planningcenter as pcm  # noqa: E402
from app.modules.youtube import routes as yroutes  # noqa: E402
from app.modules.youtube import schedule as sch  # noqa: E402
from app.modules.youtube import service as svc  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

TZ = ZoneInfo("America/New_York")
asyncio.set_event_loop(asyncio.new_event_loop())


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def ts(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- fakes
class FakeYouTube:
    """Stands in for Planner.api: a list of upcoming broadcasts, and what was asked."""

    def __init__(self):
        self.upcoming: list[dict] = []
        self.asked: list[tuple[str, str, object]] = []
        self.fail: str | None = None

    async def api(self, planner, method, path, body=None):
        self.asked.append((method, path.split("?")[0], body))
        if self.fail:
            raise sch.Problem(self.fail)
        if path.startswith("liveBroadcasts?part=id,snippet,status,contentDetails&broadcastStatus=upcoming"):
            return {"items": [{"id": b["id"], "snippet": {"title": b["title"], "scheduledStartTime": iso(b["start"])},
                               "status": {"privacyStatus": "public"}, "contentDetails": {}} for b in self.upcoming]}
        if method == "POST" and path.startswith("liveBroadcasts?"):
            vid = f"made{len(self.asked):07d}"
            self.upcoming.append({"id": vid, "title": body["snippet"]["title"],
                                  "start": dt.datetime.fromisoformat(body["snippet"]["scheduledStartTime"]
                                                                     .replace("Z", "+00:00")).timestamp()})
            return {"id": vid}
        if path.startswith("liveStreams"):
            assert "cdn" not in path, "the cdn part carries the stream key"
            return {"items": [{"id": "key1", "snippet": {"title": "SERVICE"}, "cdn": {"resolution": "1080p"},
                               "status": {"streamStatus": "ready"}}]}
        return {}


YT = FakeYouTube()
sch.Planner.api = lambda self, m, p, b=None: YT.api(self, m, p, b)


class FakePC:
    def __init__(self):
        self.writes: list[tuple[str, str]] = []
        self.fail: pcm.PlanningCenterError | None = None
        self.plans: dict[str, list[dict]] = {}

    def channels(self):
        return [{"id": "25769", "name": "Live", "watch_live": True, "url": ""}]

    def episodes(self, cid):
        return [{"id": "533082", "title": "Sunday Morning", "live_at": None, "url": "",
                 "times": [{"id": "1", "starts_at": ts(2026, 11, 15, 10), "video_url": ""}]}]

    def service_types(self):
        return [{"id": "671659", "name": "Sunday Morning"}, {"id": "9", "name": "Not followed"}]

    def plans_from(self, st, after):
        return self.plans.get(st, [])

    def set_episode_video(self, eid, url):
        if self.fail:
            raise self.fail
        self.writes.append((eid, url))
        return []


PC = FakePC()
PC_ON = {"on": True}
sch.pc_client = yroutes.pc_client = lambda: PC if PC_ON["on"] else None
sch._pc_on = lambda: PC_ON["on"]

GRANTED = {"scopes": [svc.CLOUD_SCOPE]}
svc.ACCOUNT.connected = lambda: True
svc.ACCOUNT.status = lambda: {"state": "connected", "service": "YouTube", "message": "", "account": "Grace",
                              "scopes": GRANTED["scopes"], "connected_at": 1, "connected_by": "owner"}

config.ensure_file()
config.save({"modules": {"youtube": {"enabled": True}}})
registry.attach("youtube")
registry.attach("planning_center")          # YouTube asks Planning Center for its client by name (#523)
P = sch.planner
P.lock = asyncio.Lock()
NOW = time.time()


def link(**kw):
    base = {"id": "l1", "channel_id": "25769", "channel": "Live", "episode_id": "533082", "episode": "Sunday Morning",
            "slot": ts(2026, 11, 15, 10), "match": "date", "video": "", "auto": True}
    return {**base, **kw}


# ---------------------------------------------------------------- pure parts
with section("settings are cleaned"):
    s = sch.clean_schedule({"on": "yes", "privacy": "secret", "days_ahead": 99, "stream_id": "a b",
                            "plan_types": ["12", "x/../1", 5], "title": "",
                            "rules": [{"day": 6, "time": "9:30", "name": " Sunday "}, {"day": 7, "time": "10:00"},
                                      {"day": 1, "time": "25:00"}, "junk"]})
    eq("on only when true", s["on"], False)
    eq("privacy falls back", s["privacy"], "public")
    eq("days clamped", s["days_ahead"], 14)
    eq("a stream id with a space is dropped", s["stream_id"], "")
    eq("only numeric plan types", s["plan_types"], ["12"])
    eq("blank title gets the default", s["title"], "{name} · {date}")
    eq("one good rule, time padded; saved before #522, so Sunday 6 (from Monday) is renumbered 0",
       [(r["day"], r["time"], r["name"]) for r in s["rules"]], [(0, "09:30", "Sunday")])
    eq("marked as counted from Sunday", s["days_from"], "sunday")
    eq("cleaning again does not renumber twice", [r["day"] for r in sch.clean_schedule(s)["rules"]], [0])
    fresh = sch.clean_schedule({"days_from": "sunday", "rules": [{"day": 0, "time": "09:00", "name": "Sun"},
                                                                 {"day": 6, "time": "18:00", "name": "Sat"}]})
    eq("days saved from Sunday are kept", [r["day"] for r in fresh["rules"]], [0, 6])
    check("the rule got an id", bool(s["rules"][0]["id"]))
    eq("no settings is the default", sch.clean_schedule(None), svc.SCHEDULE)
    links = sch.clean_links([link(match="bogus", video="bad id!"), {"channel_id": "1/2", "episode_id": "3"}, link(id="l2", auto=False)])
    eq("bad links dropped", len(links), 2)
    eq("bad match falls back to date", links[0]["match"], "date")
    eq("bad video id dropped", links[0]["video"], "")
    eq("auto off kept", links[1]["auto"], False)
    eq("created keys are checked", sch.clean_created({"plan:1": {"id": "abcdefgh", "at": 5}, "rule:x:2026-1-1": {"id": "abcdefgh", "at": 5},
                                                      "plan:2": {"id": "no", "at": 1}}),
       {"plan:1": {"id": "abcdefgh", "at": 5.0, "made": False}})
    pub = svc.public(svc.clean_settings({"schedule": {"on": True}, "links": [link()]}))
    check("the general read leaves the schedule to its own route", "schedule" not in pub and "links" not in pub)

with section("when services are"):
    now = ts(2026, 11, 17, 12)                                  # a Tuesday
    rules = sch.clean_schedule({"rules": [{"id": "sun", "day": 6, "time": "10:00", "name": "Sunday"},
                                          {"id": "tue", "day": 1, "time": "09:00", "name": "Prayer"}]})["rules"]
    got = sch.rule_times(rules, now, 7, TZ)
    eq("Sunday this week; Tuesday's has passed, next Tuesday is in the week",
       [g["key"] for g in got], ["rule:sun:2026-11-22", "rule:tue:2026-11-24"])
    eq("local time", got[0]["start"], ts(2026, 11, 22, 10))
    eq("plan start: the service time", sch.plan_start({"times": [{"type": "rehearsal", "starts_at": 5}, {"type": "service", "starts_at": 9}]}), 9)
    eq("plan start: sort date with no times", sch.plan_start({"times": [], "sort_date": 7}), 7)
    eq("title", sch.title_for("{name} · {date} {time}", {"name": "Sunday", "start": ts(2026, 11, 22, 10)}, TZ),
       "Sunday · November 22, 2026 10:00 AM")
    eq("title is at most 100", len(sch.title_for("x" * 300, {"name": "S", "start": now}, TZ)), 100)

with section("which broadcast an episode gets"):
    now = ts(2026, 11, 17, 12)
    c = [{"id": "wedwedwed1", "title": "Wed", "start": ts(2026, 11, 18, 19)},
         {"id": "sunsunsun1", "title": "Sun", "start": ts(2026, 11, 22, 10)},
         {"id": "sunsunsun2", "title": "Sun 2", "start": ts(2026, 11, 29, 10)}]
    eq("recurring episode (last week's time): the next Sunday at 10", sch.pick(link(), c, TZ, now)["id"], "sunsunsun1")
    eq("Wednesday episode", sch.pick(link(slot=ts(2026, 11, 11, 19)), c, TZ, now)["id"], "wedwedwed1")
    eq("an episode time ahead must be that day", sch.pick(link(slot=ts(2026, 11, 29, 10, 30)), c, TZ, now)["id"], "sunsunsun2")
    eq("no match", sch.pick(link(slot=ts(2026, 11, 13, 7)), c, TZ, now), None)
    eq("next", sch.pick(link(match="next"), c, TZ, now)["id"], "wedwedwed1")
    eq("manual", sch.pick(link(match="manual", video="sunsunsun2"), c, TZ, now)["id"], "sunsunsun2")
    eq("manual, not listed any more, still that one", sch.pick(link(match="manual", video="gonegone12"), c, TZ, now)["id"], "gonegone12")

# ---------------------------------------------------------------- the planner
with section("nothing set up: no quota spent"):
    run(P.run())
    eq("not asked", YT.asked, [])
    run(P.run(asked=True))
    eq("Check now lists anyway", [a[1] for a in YT.asked], ["liveBroadcasts"])

with section("creates each service's broadcast once, bound to the stream key"):
    YT.asked.clear()
    soon = time.time() + 3 * 86400
    when = dt.datetime.fromtimestamp(soon, TZ)
    YT.upcoming = [{"id": "already001", "title": "Made in Studio", "start": time.time() + 86400 * 5}]
    config.save({"youtube": {"schedule": {"on": True, "privacy": "unlisted", "stream_id": "key1",
                                          "days_from": "sunday",
                                          "rules": [{"id": "r1", "day": timewin.weekday(when), "time": when.strftime("%H:%M"), "name": "Prayer"}],
                                          "plan_types": ["671659"]}}})
    plan_at = time.time() + 86400 * 5 + 600                     # ten minutes from the one made in Studio
    PC.plans = {"671659": [{"id": "77", "title": "Advent 1", "series_title": "", "sort_date": plan_at, "times": []},
                           {"id": "78", "title": "Far", "series_title": "", "sort_date": time.time() + 86400 * 30, "times": []}]}
    run(P.run())
    posts = [a for a in YT.asked if a[0] == "POST"]
    eq("one insert and one bind", [a[1] for a in posts], ["liveBroadcasts", "liveBroadcasts/bind"])
    body = posts[0][2]
    eq("privacy", body["status"]["privacyStatus"], "unlisted")
    eq("starts and stops with the encoder", (body["contentDetails"]["enableAutoStart"], body["contentDetails"]["enableAutoStop"]), (True, True))
    check("titled from the rule", body["snippet"]["title"].startswith("Prayer · "))
    created = sch.stored()["created"]
    eq("the plan took the broadcast already made in Studio", created["plan:77"], {"id": "already001", "at": plan_at, "made": False})
    eq("the rule's was made by Relay", sorted(k.split(":")[0] for k, v in created.items() if v["made"]), ["rule"])
    check("the far plan is not made yet", "plan:78" not in created)
    YT.asked.clear()
    run(P.run())
    eq("second run creates nothing", [a for a in YT.asked if a[0] == "POST"], [])
    snap = P.snapshot()
    eq("upcoming marks Relay's", sorted(b["relay"] for b in snap["upcoming"]), [False, True])

with section("read-only sign-in: says so, creates nothing"):
    GRANTED["scopes"] = [svc.READ_SCOPE]
    config.save({"youtube": {"created": {}}})
    YT.asked.clear()
    run(P.run())
    eq("no insert", [a for a in YT.asked if a[0] == "POST"], [])
    check("tells the operator to connect again", "Connect again" in P.problem)
    GRANTED["scopes"] = [svc.CLOUD_SCOPE]
    config.save({"youtube": {"schedule": {"on": False}}})

with section("sends a link as soon as its broadcast is scheduled, once"):
    sun = time.time() + 2 * 86400
    YT.upcoming = [{"id": "sunday0001", "title": "Sunday", "start": sun}]
    config.save({"youtube": {"links": sch.clean_links([link(slot=sun - 7 * 86400)])}})
    PC.writes.clear()
    run(P.run())
    eq("written", PC.writes, [("533082", "https://www.youtube.com/watch?v=sunday0001")])
    eq("remembered", sch.stored()["links"][0]["pushed"], "sunday0001")
    run(P.run())
    eq("not again", len(PC.writes), 1)
    out = run(P.push("l1"))
    eq("Send writes again", (out["ok"], len(PC.writes)), (True, 2))

with section("send: refusals are fixed sentences"):
    PC_ON["on"] = False
    eq("Planning Center off", run(P.push("l1")), {"ok": False, "error": sch.NO_PC})
    PC_ON["on"] = True
    PC.fail = pcm.PlanningCenterError("auth", "secret detail from the server", status=403)
    out = run(P.push("l1"))
    eq("Planning Center refused: its operator sentence", out["error"], pcm.operator_message(PC.fail))
    check("never the exception's text", "secret detail" not in out["error"])
    PC.fail = None
    YT.upcoming = []
    run(P.run())
    eq("no broadcast", run(P.push("l1"))["error"], "No upcoming YouTube broadcast matches this episode yet.")
    eq("unknown link", run(P.push("zzz"))["ok"], False)

with section("YouTube refusals: fixed sentences"):
    YT.fail = "quota"
    run(P.run(asked=True))
    eq("quota", P.problem, sch.WORDS["quota"])
    YT.fail = None
    svc.service.quota_until = 0
    eq("an unknown key is 'other'", sch.Problem("nope").words, sch.WORDS["other"])
    eq("sign-in trouble", sch.words(oauth.NeedsSignIn("x")), sch.WORDS["sign_in"])

# ---------------------------------------------------------------- routes
with section("routes and permissions"):
    owner = _db.client(app, base_url="http://relay.local:8001")
    operator = _db.client(app, "operator", base_url="http://relay.local:8001")
    viewer = _db.client(app, "viewer", base_url="http://relay.local:8001")
    eq("viewer reads the schedule", viewer.get("/api/admin/youtube/schedule").status_code, 200)
    eq("viewer cannot change it", viewer.put("/api/admin/youtube/schedule", json={"on": True}).status_code, 403)
    r = owner.put("/api/admin/youtube/schedule", json={"days_ahead": 3, "privacy": "private", "junk": 1})
    eq("owner saves", (r.status_code, r.json()["schedule"]["days_ahead"], r.json()["schedule"]["privacy"]), (200, 3, "private"))
    r = owner.put("/api/admin/youtube/schedule", json={"days_from": "monday",
                                                      "rules": [{"id": "s1", "day": 0, "time": "09:00", "name": "Sun"}]})
    eq("a day the page sends counts from Sunday, whatever days_from it claims", [x["day"] for x in r.json()["schedule"]["rules"]], [0])
    eq("and it is stored that way", [x["day"] for x in sch.stored()["schedule"]["rules"]], [0])
    eq("stream keys", owner.get("/api/admin/youtube/streams").json()["streams"][0]["title"], "SERVICE")
    pub = owner.get("/api/admin/youtube/publishing").json()
    eq("channels", [c["name"] for c in pub["channels"]], ["Live"])
    config.save({"planning_center": {"service_types": ["671659"]}})
    eq("only followed service types", [t["id"] for t in owner.get("/api/admin/youtube/publishing").json()["service_types"]], ["671659"])
    eq("episodes", owner.get("/api/admin/youtube/publishing/25769/episodes").json()["episodes"][0]["id"], "533082")
    eq("a channel id that is not one", owner.get("/api/admin/youtube/publishing/2x/episodes").status_code, 404)
    PC_ON["on"] = False
    eq("Planning Center off", owner.get("/api/admin/youtube/publishing").status_code, 409)
    PC_ON["on"] = True
    saved = sch.stored()["links"]
    r = owner.put("/api/admin/youtube/links", json={"links": [dict(saved[0], pushed="forged0001"), link(id="new1", episode_id="607878")]})
    after = {x["id"]: x for x in r.json()["links"]}
    eq("an unchanged link keeps what was sent, not what the browser said", after["l1"]["pushed"], saved[0]["pushed"])
    eq("a new link has sent nothing", after["new1"]["pushed"], "")
    r = owner.put("/api/admin/youtube/links", json={"links": [dict(after["l1"], match="next")]})
    eq("changing which broadcast forgets the last send", r.json()["links"][0]["pushed"], "")
    YT.upcoming = [{"id": "sunday0002", "title": "Sunday", "start": time.time() + 86400}]
    run(P.run(push=False))
    eq("viewer cannot send", viewer.post("/api/admin/youtube/links/l1/send").status_code, 403)
    r = operator.post("/api/admin/youtube/links/l1/send")
    eq("operator sends", (r.status_code, r.json()["sent"]["video"]), (200, "sunday0002"))
    eq("a bad link id", operator.post("/api/admin/youtube/links/BAD!/send").status_code, 404)
    eq("operator cannot change links", operator.put("/api/admin/youtube/links", json={"links": []}).status_code, 403)

# ---------------------------------------------------------------- the Planning Center client
with section("Publishing calls against a fake Planning Center"):
    seen = []

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(("GET", self.path, None))
            if self.path.startswith("/publishing/v2/channels?"):
                return self._send({"data": [{"id": "1", "attributes": {"name": "Live", "enable_watch_live": True}},
                                            {"id": "2", "attributes": {"name": "Old", "archived": True}}]})
            if self.path.startswith("/publishing/v2/channels/1/episodes"):
                return self._send({"data": [{"id": "10", "attributes": {"title": "Sunday", "published_live_at": "2026-11-15T14:45:00Z"},
                                             "relationships": {"episode_times": {"data": [{"type": "EpisodeTime", "id": "100"}]}}}],
                                   "included": [{"type": "EpisodeTime", "id": "100", "attributes": {
                                       "starts_at": "2026-11-15T15:00:00Z", "video_url": "https://www.youtube.com/watch?v=old"}}]})
            if self.path.startswith("/publishing/v2/episodes/10/episode_times"):
                return self._send({"data": [{"type": "EpisodeTime", "id": "100", "attributes": {"video_url": "https://www.youtube.com/watch?v=old"}},
                                            {"type": "EpisodeTime", "id": "101", "attributes": {"video_url": "https://www.youtube.com/watch?v=new"}}]})
            self._send({"data": []})

        def do_PATCH(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("PATCH", self.path, body, self.headers.get("Content-Type")))
            self._send({"data": {"type": "EpisodeTime", "id": body["data"]["id"],
                                 "attributes": {"video_url": body["data"]["attributes"]["video_url"], "streaming_service": "youtube"}}})

    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c = pcm.Client("id", "secret", base_url=f"http://127.0.0.1:{server.server_port}")
    eq("archived channels left out", [(x["id"], x["watch_live"]) for x in c.channels()], [("1", True)])
    eps = c.episodes("1")
    eq("episode with its included time", (eps[0]["title"], eps[0]["times"][0]["video_url"]), ("Sunday", "https://www.youtube.com/watch?v=old"))
    out = c.set_episode_video("10", "https://www.youtube.com/watch?v=new")
    patches = [s for s in seen if s[0] == "PATCH"]
    eq("only the time without the link is changed", [p[1] for p in patches], ["/publishing/v2/episodes/10/episode_times/100"])
    eq("JSON:API body", patches[0][2], {"data": {"type": "EpisodeTime", "id": "100", "attributes": {"video_url": "https://www.youtube.com/watch?v=new"}}})
    eq("sent as JSON", patches[0][3], "application/json")
    eq("both times now carry it", [t["video_url"] for t in out], ["https://www.youtube.com/watch?v=new"] * 2)
    try:
        c.episodes("1/../../services")
        check("refused a path", False)
    except pcm.PlanningCenterError:
        check("an id that is not digits never reaches a path", True)
    server.shutdown()

done()
