"""Planning Center: following today's plan and its LIVE state.

Which plan is today's (day boundary in the organisation's zone, a morning and
an evening plan, the operator's pick), the LIVE poll and its rate across a full
Sunday with three service types, and the timers Relay works out.

Offline. The fake answers in the JSON:API shapes the Planning Center docs give
for Plan, PlanTime, Live, ItemTime, Item, ItemNote, Key, Arrangement and
Organization; they were written from the docs, not captured from a live
account (tools/check_planningcenter.py --live checks the real thing).

Run: python tests/test_pclive.py
"""
import asyncio
import os
import re
import sys
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pg  # noqa: E402  (a throwaway Postgres for the user system; must come before app.main)

_pg.start()
_pg.reset()
os.environ["TZ"] = "America/Chicago"   # Relay's own zone differs from the organisation's on purpose

from fastapi.testclient import TestClient

from app import config
from app.modules.planning_center import pclive, planningcenter as pc
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


NY = ZoneInfo("America/New_York")


def at(h, m=0, s=0, d=8):
    """A wall-clock time in New York on Sunday 8 November 2026 (or day `d`)."""
    return datetime(2026, 11, d, h, m, s, tzinfo=NY).timestamp()


def iso(ts):
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


# -- the fake account ---------------------------------------------------------
# service type -> plans: (plan id, title, [(time id, type, start, end)])
PLANS = {
    "7": [("700", "Sunday Morning", [("7001", "service", at(10), at(11, 30))]),
          ("701", "Next week", [("7011", "service", at(10, d=15), at(11, 30, d=15))])],
    "42": [("420", "Sunday Evening", [("4201", "service", at(18), at(19, 15))])],
    # One service type with a Saturday-night plan (already Sunday in UTC), then
    # a morning and an evening plan on the same Sunday.
    "50": [("900", "Saturday Night", [("9001", "service", at(20, d=7), at(21, d=7))]),
           ("901", "Morning", [("9010", "rehearsal", at(7, 30), at(8, 30)),
                               ("9011", "service", at(9), at(10, 15))]),
           ("902", "Evening", [("9021", "service", at(18, 30), at(19, 30))]),
           ("903", "Next week", [("9031", "service", at(9, d=15), at(10, 15, d=15))])],
}
NAMES = {"7": "Sunday AM", "42": "Sunday PM", "50": "Chapel"}
# The items every plan has: (suffix, title, type, length in seconds, position)
ITEMS = [("1", "Countdown", "media", 300, "pre"), ("2", "Welcome", "item", 300, "during"),
         ("3", "Great Song", "song", 240, "during"), ("4", "Sermon", "item", 1500, "during")]


class World:
    """What Planning Center knows right now. `live[plan]` is
    {"controller": name or None, "current": item index or None, "started": epoch}."""

    def __init__(self):
        self.now = 0.0
        self.live = {}
        self.pt_live = {}        # plan time id -> (live_starts_at, live_ends_at)
        self.calls = []          # (sim time, path)
        self.window = deque()
        self.peak = 0            # most calls seen in any 20 s
        self.unlinked = False    # leave plan_times out of Plan relationships
        self.fail = None
        self.zone = "Eastern Time (US & Canada)"


world = World()


def plan_row(st, pid, title, times, linked=True):
    row = {"type": "Plan", "id": pid, "attributes": {
        "title": title, "series_title": "Autumn", "dates": utc(times[0][2]).astimezone(NY).strftime("%B %-d, %Y"),
        "sort_date": iso(min(t[2] for t in times if t[1] == "service")), "updated_at": "2026-11-01T12:00:00Z"},
        "relationships": {"service_type": {"data": {"type": "ServiceType", "id": st}}}}
    if linked:
        row["relationships"]["plan_times"] = {"data": [{"type": "PlanTime", "id": t[0]} for t in times]}
    return row


def time_rows(times):
    out = []
    for tid, kind, start, end in times:
        ls, le = world.pt_live.get(tid, (None, None))
        out.append({"type": "PlanTime", "id": tid, "attributes": {
            "time_type": kind, "name": "", "starts_at": iso(start), "ends_at": iso(end),
            "live_starts_at": iso(ls), "live_ends_at": iso(le)}})
    return out


def find_plan(st, pid):
    return next(p for p in PLANS[st] if p[0] == pid)


def item_time(pid, idx, tid, started=None):
    return {"type": "ItemTime", "id": f"{pid}{ITEMS[idx][0]}-{tid}", "attributes": {
        "exclude": False, "live_start_at": iso(started), "live_end_at": None},
        "relationships": {"item": {"data": {"type": "Item", "id": pid + ITEMS[idx][0]}},
                          "plan_time": {"data": {"type": "PlanTime", "id": tid}},
                          "plan": {"data": {"type": "Plan", "id": pid}}}}


class FakeClient(pc.Client):
    """The real client with the network swapped for the fake account."""

    def __init__(self):
        super().__init__("id", "secret")

    def get(self, path, params=None):
        params = params or {}
        world.calls.append((world.now, path))
        world.window.append(world.now)
        while world.window and world.window[0] <= world.now - 20:
            world.window.popleft()
        world.peak = max(world.peak, len(world.window))
        self.rate = {"count": len(world.window), "limit": 100, "period": 20}
        if world.fail:
            raise world.fail
        if path == "/services/v2":
            return {"data": {"type": "Organization", "id": "1", "attributes": {"time_zone": world.zone}}}
        if path == "/services/v2/me":
            return {"data": {"type": "Person", "id": "5", "attributes": {"full_name": "Dana Ortiz"}}}
        if path == "/services/v2/service_types":
            return {"data": [{"type": "ServiceType", "id": i, "attributes": {"name": n}} for i, n in NAMES.items()]}
        m = re.fullmatch(r"/services/v2/service_types/(\d+)/plans(?:/(\d+)(?:/(live|items|plan_times))?)?", path)
        st, pid, leaf = m.groups()
        if pid is None:
            after = pc.parse_time(params["after"])
            assert params["filter"] == "after" and params["order"] == "sort_date"
            rows = [p for p in PLANS[st] if any(t[2] > after for t in p[2])]
            return {"data": [plan_row(st, *p, linked=not world.unlinked) for p in rows],
                    "included": [r for p in rows for r in time_rows(p[2])]}
        _, title, times = find_plan(st, pid)
        if leaf is None:
            return {"data": plan_row(st, pid, title, times), "included": time_rows(times)}
        if leaf == "plan_times":
            return {"data": time_rows(times)}
        tid = next(t[0] for t in times if t[1] == "service")
        if leaf == "items":
            assert params["include"] == "item_times,key,arrangement,item_notes"
            data, inc = [], []
            for n, (suffix, name, kind, length, pos) in enumerate(ITEMS):
                row = {"type": "Item", "id": pid + suffix, "attributes": {
                    "title": name, "item_type": kind, "length": length, "sequence": n + 1,
                    "service_position": pos, "key_name": "G" if kind == "song" else None,
                    "custom_arrangement_sequence": ["Verse 1", "Chorus 1"] if kind == "song" else None},
                    "relationships": {"plan": {"data": {"type": "Plan", "id": pid}},
                                      "arrangement": {"data": {"type": "Arrangement", "id": "55"} if kind == "song" else None},
                                      "key": {"data": {"type": "Key", "id": "66"} if kind == "song" else None}}}
                data.append(row)
                inc.append(item_time(pid, n, tid))
                if kind == "song":
                    inc.append({"type": "ItemNote", "id": pid + "n", "attributes": {
                        "category_name": "Vocals", "content": "Ask Sam to lead"},
                        "relationships": {"item": {"data": {"type": "Item", "id": pid + suffix}}}})
            inc += [{"type": "Arrangement", "id": "55", "attributes": {"name": "Radio", "sequence": ["Intro"]}},
                    {"type": "Key", "id": "66", "attributes": {"name": "G"}}]
            return {"data": data, "included": inc}
        live = world.live.get(pid)
        if live is None:  # LIVE was never opened for this plan
            raise pc.PlanningCenterError("http", "Planning Center answered HTTP 404: not found", status=404)
        cur, inc = live["current"], []
        rel = {"controller": {"data": None}, "current_item_time": {"data": None}, "next_item_time": {"data": None}}
        if live["controller"]:
            rel["controller"]["data"] = {"type": "Person", "id": "5"}
            inc.append({"type": "Person", "id": "5", "attributes": {"full_name": live["controller"]}})
        if cur is not None:
            row = item_time(pid, cur, tid, live["started"])
            rel["current_item_time"]["data"] = {"type": "ItemTime", "id": row["id"]}
            inc.append(row)
            if cur + 1 < len(ITEMS):
                nxt = item_time(pid, cur + 1, tid)
                rel["next_item_time"]["data"] = {"type": "ItemTime", "id": nxt["id"]}
                inc.append(nxt)
        return {"data": {"type": "Live", "id": pid, "attributes": {"title": title}, "relationships": rel},
                "included": inc}


def press(pid, idx, when, controller="Dana Ortiz"):
    """Someone takes control, or presses Next, at `when`."""
    world.live[pid] = {"controller": controller, "current": idx, "started": when if idx is not None else None}


def follow(ids, overrides=None):
    config.save({"planning_center": {"app_id": "id", "secret": "secret", "service_types": ids,
                                     "plan_overrides": overrides or {}}}, replace=("planning_center",))


def new_follower():
    f = pclive.Follower()
    f.make_client = lambda cfg: FakeClient()
    return f


async def tick(f, ts):
    world.now = ts
    await f.tick(utc(ts))


async def run_until(f, start, end, events=(), watch=None):
    """Step simulated time from start to end, firing (time, fn) events, jumping
    over stretches in which the follower has nothing due."""
    events = sorted(events, key=lambda e: e[0])
    t, i = start, 0
    while t <= end:
        while i < len(events) and events[i][0] <= t:
            events[i][1]()
            i += 1
        await tick(f, t)
        if watch:
            watch(t)
        nxt = min(f.next_due(), events[i][0] if i < len(events) else end + 1, t + 60)
        t = max(t + pclive.TICK_S, nxt)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"planning_center": {"enabled": True}}})  # the module is switched on (#157)
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")  # its hooks into core, without its background work


async def main():
    print("pure: which plan is today's")
    f = FakeClient()
    world.now = at(8)
    plans = f.plans_from("50", utc(at(0)))
    eq("plans and their times are read", [(p["id"], [t["type"] for t in p["times"]]) for p in plans][:2],
       [("901", ["rehearsal", "service"]), ("902", ["service"])])
    plans = f.plans_from("50", utc(at(12, d=7)))
    eq("Saturday night (already Sunday in UTC) is not Sunday's plan",
       pc.pick_plan(plans, utc(at(0, 30)), NY)["id"], "901")
    eq("…and it is Saturday's, until local midnight", pc.pick_plan(plans, utc(at(23, 59, d=7)), NY)["id"], "900")
    eq("morning plan in the morning", pc.pick_plan(plans, utc(at(8)), NY)["id"], "901")
    eq("a rehearsal time does not count as the service", pc.near_service(plans[1], at(7, 45), 0), False)
    eq("evening plan once the morning is over", pc.pick_plan(plans, utc(at(13)), NY)["id"], "902")
    eq("the last plan stays after the last service", pc.pick_plan(plans, utc(at(23)), NY)["id"], "902")
    eq("no plan on a day without a service", pc.pick_plan(plans, utc(at(12, d=10)), NY), None)
    world.pt_live["9011"] = (at(9), None)
    over = f.plans_from("50", utc(at(12, d=7)))
    eq("a morning service still running at its planned end keeps its plan",
       pc.pick_plan(over, utc(at(10, 40)), NY)["id"], "901")
    world.pt_live.clear()
    world.unlinked = True
    eq("plan_times that come back unlinked are fetched per plan",
       pc.pick_plan(f.plans_from("50", utc(at(0))), utc(at(8)), NY)["id"], "901")
    world.unlinked = False
    eq("Rails zone name", pc.zone_for("Eastern Time (US & Canada)").key, "America/New_York")
    eq("IANA zone name", pc.zone_for("America/Chicago").key, "America/Chicago")
    eq("unknown zone name", pc.zone_for("Middle Earth"), None)

    print("pure: timers")
    items = f.items("7", "700")
    eq("items in order with key, arrangement and notes",
       [(i["title"], i["type"], i["length"], i["position"]) for i in items],
       [(n, k, l, p) for _, n, k, l, p in ITEMS])
    song = items[2]
    eq("song detail", (song["key"], song["arrangement"], song["arrangement_sequence"], song["notes"]),
       ("G", "Radio", ["Verse 1", "Chorus 1"], [{"category": "Vocals", "content": "Ask Sam to lead"}]))
    plan = f.plan("7", "700")
    start = at(10)
    press("700", 2, start + 330)   # Welcome ran 30 s long; the song started at 10:05:30
    world.pt_live["7001"] = (start, None)
    plan, live = f.plan("7", "700"), f.live("7", "700")
    t = pc.timers(plan, items, live, start + 430)
    eq("item time left = start + length - now", t["item_left_s"], 140.0)
    eq("service is 30 s behind", t["service_over_s"], 30.0)
    t = pc.timers(plan, items, live, start + 630)
    eq("item over its length goes negative", t["item_left_s"], -60.0)
    eq("…and the overrun adds to the service", t["service_over_s"], 90.0)
    eq("current and next item named", (t["current_item"], t["next_item"]), ("7003", "7004"))
    press("700", 0, start - 120)
    t = pc.timers(plan, items, f.live("7", "700"), start - 60)
    eq("a pre-service item has time left but no over/under", (t["item_left_s"], t["service_over_s"]), (240.0, None))
    world.live.clear()
    world.pt_live.clear()
    live = f.live("7", "700")
    eq("LIVE never opened (404) is not live, not an error", (live["controller"], live["current"]), (None, None))
    eq("nothing running: no timers", pc.timers(plan, items, live, start)["item_left_s"], None)

    print("a full Sunday, three service types")
    follow(["7", "42", "50"])
    fo = new_follower()
    world.calls.clear()
    world.window.clear()
    world.peak = 0
    seen_next = {}     # (plan, item index) -> seconds until the follower showed it
    pending = {}

    def nxt(pid, idx, when):
        def fire():
            press(pid, idx, when)
            if idx is not None:
                pending[(pid, idx)] = when
        return (when, fire)

    def end(pid, tid, start, when):
        def fire():
            world.live[pid] = {"controller": None, "current": None, "started": None}
            world.pt_live[tid] = (start, when)
        return (when, fire)

    def begin(tid, when):
        return (when, lambda: world.pt_live.__setitem__(tid, (when, None)))

    events = []
    for st, pid, tid, s in (("50", "901", "9011", at(9)), ("7", "700", "7001", at(10)),
                            ("42", "420", "4201", at(18)), ("50", "902", "9021", at(18, 30))):
        events += [nxt(pid, 0, s - 120), begin(tid, s), nxt(pid, 1, s), nxt(pid, 2, s + 330),
                   nxt(pid, 3, s + 570.25), end(pid, tid, s, s + 4500)]
    by_plan = {"901": "50", "700": "7", "420": "42", "902": "50"}
    picks = {}

    def watch(t):
        for (pid, idx), when in list(pending.items()):
            st = fo.state(by_plan[pid], t)
            if st["plan"] and st["plan"]["id"] == pid and st["timers"]["current_item"] == pid + ITEMS[idx][0]:
                seen_next[(pid, idx)] = t - when
                del pending[(pid, idx)]
        hour = utc(t).astimezone(NY).hour
        picks.setdefault(hour, {s: (fo.state(s, t)["plan"] or {}).get("id") for s in ("7", "42", "50")})

    await run_until(fo, at(0, 0, 1), at(23, 59), events, watch)
    eq("06:00 follows the morning plans", picks[6], {"7": "700", "42": "420", "50": "901"})
    eq("14:00 has moved on to the evening plan", picks[14]["50"], "902")
    eq("every Next was seen", len(seen_next), 16)
    worst = max(seen_next.values())
    check("state updates within ~2 s of Next", worst <= 2.5, f"worst {worst:.2f} s")
    check("no 429s: never more than half the limit in any 20 s", world.peak <= 50, f"peak {world.peak}")
    live_calls = [t for t, p in world.calls if p.endswith("/live")]
    quiet = [t for t in live_calls if at(2) <= t < at(7)]     # nothing within an hour, nothing live
    busy = [t for t in live_calls if at(10, 5) <= t < at(10, 6)]
    check("slow when nothing is live", len(quiet) <= 3 * (5 * 3600 / pclive.SLOW_S) + 3, f"{len(quiet)} calls in 5 h")
    check("fast during a service", 25 <= len(busy) <= 65, f"{len(busy)} calls in a minute")
    eq("the organisation's zone sets the day, not Relay's", fo.snapshot()["time_zone"], "America/New_York")
    st = fo.state("7", at(23, 59))
    eq("after the service: not live, no error", (st["live"], st["error"], st["polling"]), (False, None, "slow"))

    print("during a service")
    world.live.clear()
    world.pt_live.clear()
    fo = new_follower()
    follow(["7"])
    await tick(fo, at(9, 30))
    st = fo.state("7", at(9, 30))
    eq("an hour before: fast, plan chosen, not live", (st["polling"], st["plan"]["id"], st["live"], st["error"]),
       ("fast", "700", False, None))
    press("700", 1, at(10))
    world.pt_live["7001"] = (at(10), None)
    await run_until(fo, at(10), at(10, 0, 3))
    st = fo.state("7", at(10, 0, 2))
    eq("live, with the controller's name", (st["live"], st["controller"]["name"]), (True, "Dana Ortiz"))
    eq("timers from the service's live_starts_at", (st["timers"]["item_left_s"], st["timers"]["service_over_s"]),
       (298.0, 0.0))
    world.live["700"]["controller"] = None
    await run_until(fo, at(10, 0, 4), at(10, 0, 7))
    st = fo.state("7", at(10, 0, 7))
    eq("controller gone: 'not live', timers still run", (st["live"], st["error"], st["timers"]["item_left_s"]),
       (False, None, 293.0))
    snap = fo.snapshot(at(10, 0, 7))
    check("admin snapshot leaves item notes out", "Ask Sam" not in str(snap) and "Ask Sam" in str(fo.state("7")))

    print("Planning Center unreachable")
    world.fail = pc.PlanningCenterError("network", "Could not reach Planning Center: timed out")
    await run_until(fo, at(10, 0, 8), at(10, 0, 12))
    st = fo.state("7", at(10, 0, 12))
    eq("said plainly, last state kept", (st["reachable"], "Could not reach" in st["error"], st["plan"]["id"]),
       (False, True, "700"))
    before = len(world.calls)
    await run_until(fo, at(10, 0, 13), at(10, 0, 25))
    check("and not hammered while down", len(world.calls) - before <= 2, f"{len(world.calls) - before} calls")
    world.fail = None
    await run_until(fo, at(10, 0, 26), at(10, 1))
    eq("recovers", (fo.state("7")["reachable"], fo.state("7")["error"]), (True, None))

    print("midnight")
    press("700", 3, at(23, 50))
    await run_until(fo, at(23, 50), at(0, 10, d=9))
    eq("a service still running at midnight keeps its plan", fo.state("7")["plan"]["id"], "700")
    world.live["700"] = {"controller": None, "current": None, "started": None}
    await run_until(fo, at(0, 10, 1, d=9), at(0, 20, d=9))
    st = fo.state("7")
    eq("then the new day has no plan", (st["plan"], st["day"]), (None, "2026-11-09"))

    print("operator's pick")
    world.live.clear()
    follow(["50"], {"50": {"plan": "902", "day": "2026-11-08"}})
    fo = new_follower()
    await tick(fo, at(8))
    st = fo.state("50", at(8))
    eq("the picked plan is followed instead of the morning one", (st["plan"]["id"], st["override"]), ("902", True))
    await tick(fo, at(8, d=9))
    eq("the pick lapses with the day", fo.state("50")["override"], False)
    follow([])
    await tick(fo, at(9))
    eq("nothing followed: nothing tracked", fo.snapshot()["service_types"], [])
    before = len(world.calls)
    await tick(fo, at(9, 0, 30))
    eq("…and nothing asked of Planning Center", len(world.calls), before)

asyncio.run(main())

print("admin")
pclive.follower.make_client = lambda cfg: FakeClient()
world.now = at(12, d=10)
admin = _pg.client(app, base_url="http://relay.local:8001")
URL = "/api/admin/planning-center"
admin.post(URL, json={"app_id": "id", "secret": "secret"})
admin.post(URL + "/service-types", json={"ids": ["50"]})
snap = admin.get(URL + "/live").json()
eq("followed type listed with the plans to pick from",
   [(t["name"], t["plan"], [p["id"] for p in t["plans"]]) for t in snap["service_types"]],
   [("Chapel", None, ["903"])])
eq("picking for a type that is not followed is refused",
   admin.post(URL + "/plan", json={"service_type": "7", "plan": "700"}).status_code, 400)
eq("a plan id must be a number",
   admin.post(URL + "/plan", json={"service_type": "50", "plan": "x"}).status_code, 400)
r = admin.post(URL + "/plan", json={"service_type": "50", "plan": "903"})
t = r.json()["service_types"][0]
eq("the operator's pick is followed at once", (t["plan"]["id"], t["override"]), ("903", True))
eq("…and survives a blank token save", (admin.post(URL, json={}).status_code,
   config.get()["planning_center"]["plan_overrides"]["50"]["plan"]), (200, "903"))
t = admin.post(URL + "/plan", json={"service_type": "50", "plan": ""}).json()["service_types"][0]
eq("clearing it goes back to Relay's pick", (t["plan"], t["override"]), (None, False))
check("secret is not in the snapshot", "secret" not in admin.get(URL + "/live").text)
check("not reachable on the viewer port",
      TestClient(app, base_url="http://relay.local:8000").get(URL + "/live").status_code in (403, 404))
admin.delete(URL)
eq("disconnect stops following", admin.get(URL + "/live").json()["service_types"], [])

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
