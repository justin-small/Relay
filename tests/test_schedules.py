"""Schedule tests: validation, occurrences, DST, overnight and merged windows,
US formatting, the start/stop rules, and the admin API.

Does not contact OpenAI or open an audio device -- the scheduler drives a fake
engine. Run: .venv/bin/python tests/test_schedules.py
"""
import asyncio, json, os, sys, tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from app import config, schedules as S
from app import scheduler as scheduler_mod

UTC = timezone.utc
CHI = ZoneInfo("America/Chicago")
NY = ZoneInfo("America/New_York")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import eq as check, fails  # noqa: E402


def raises(label, fn, fragment):
    try:
        fn()
    except ValueError as exc:
        check(label, fragment in str(exc), True)
        return
    check(label + " (did not raise)", False, True)


CT, NYT, PHX = "America/Chicago", "America/New_York", "America/Phoenix"
WINDOW_KEYS = ("repeat", "days", "start_date", "end_date", "start_time", "stop_time")


def sched(**kw):
    """A named schedule with one window; window fields as keywords, the rest (name, plan_times...) go on the schedule."""
    win = {"repeat": "weekly", "days": [0], "start_time": "10:00", "stop_time": "12:00"}
    top = {"name": "Test"}
    for k, v in kw.items():
        (win if k in WINDOW_KEYS else top)[k] = v
    return S.validate({**top, "windows": [win]})


def put(scheds, engine=True):
    """Save named schedules to the config, picked for the engine, in Central time."""
    config.save({"timezone": CT, "schedules": scheds,
                 "engine_schedules": [S.ref(x["id"]) for x in scheds] if engine else []})


def local(y, mo, d, h, mi=0, tz=CHI):
    return datetime(y, mo, d, h, mi, tzinfo=tz).astimezone(UTC)


print("validation")
s = sched(name="  Sunday   morning ")
check("name is trimmed and squeezed", s["name"], "Sunday morning")
check("a window keeps no zone", sorted(s["windows"][0]), sorted(WINDOW_KEYS))
check("a new schedule is enabled", s["enabled"], True)
check("an id is issued", len(s["id"]) > 0, True)
check("days are deduped and sorted", sched(days=[3, 0, 3])["windows"][0]["days"], [0, 3])
check("only US zones", (S.clean_zone("Europe/London"), S.clean_zone("America/Denver"), S.clean_zone(None, "America/Chicago")),
      ("America/New_York", "America/Denver", "America/Chicago"))
raises("a schedule needs a name", lambda: S.validate({"windows": [{"repeat": "weekly", "days": [0], "start_time": "10:00", "stop_time": "12:00"}]}), "name")
raises("a schedule needs a window or plan times", lambda: S.validate({"name": "x", "windows": []}), "Add a window")
check("plan times alone are enough", S.validate({"name": "x", "windows": [], "plan_times": True})["plan_times"], True)
raises("plan minutes are bounded", lambda: S.validate({"name": "x", "windows": [], "plan_times": True, "plan_before": 400}), "0 to 360")
raises("weekly needs a day", lambda: sched(days=[]), "day of the week")
raises("days stay in 0-6", lambda: sched(days=[7]), "0 (Sunday)")
raises("start and stop differ", lambda: sched(stop_time="10:00"), "must differ")
raises("24-hour times only on the wire", lambda: sched(start_time="10:00 AM"), "Start time")
raises("once needs a date", lambda: sched(repeat="once"), "needs a date")
raises("end before start", lambda: sched(start_date="2026-10-10", end_date="2026-10-01"), "before")
raises("bad date", lambda: sched(start_date="10/04/2026"), "valid date")
o = sched(repeat="once", start_date="2027-04-04", days=[1, 2], end_date="2027-05-01")
check("once drops days and end date", (o["windows"][0]["days"], o["windows"][0]["end_date"]), ([], None))

print("\nconfig load is tolerant")
cleaned = S.clean_list([
    {"repeat": "weekly", "days": [0], "start_time": "10:00", "stop_time": "12:00",
     "timezone": "America/Chicago"},
    {"repeat": "weekly", "days": [], "start_time": "10:00", "stop_time": "12:00",
     "timezone": "America/Chicago"},
    "not a schedule",
])
check("an invalid entry is dropped, not fatal", len(cleaned), 1)
check("a flat pre-#526 schedule becomes a one-window named schedule",
      (cleaned[0]["name"], len(cleaned[0]["windows"]), cleaned[0]["windows"][0]["days"]), ("Every Sun", 1, [0]))
check("and its zone is found for the one Relay zone", S.legacy_zone([{"timezone": "America/Chicago"}]), "America/Chicago")
again = S.clean_list([{"repeat": "weekly", "days": [0], "start_time": "10:00",
                       "stop_time": "12:00", "timezone": "America/Chicago"}])
check("an id-less entry gets a stable id", cleaned[0]["id"], again[0]["id"])
dup = S.clean_list([dict(s), dict(s)])
check("duplicate ids are split", dup[0]["id"] != dup[1]["id"], True)

print("\nUS formats")
check("date", S.fmt_date("2027-04-05"), "04/05/2027")
check("midnight", S.fmt_time("00:05"), "12:05 AM")
check("noon", S.fmt_time("12:00"), "12:00 PM")
check("evening", S.fmt_time("20:30"), "8:30 PM")
check("instant, with the zone's abbreviation",
      S.fmt_instant(local(2026, 9, 27, 10), "America/Chicago"), "Sun 09/27/2026 10:00 AM CDT")
check("weekly summary", S.summary(sched(days=[0, 3])),
      "Every Sun, Wed · 10:00 AM – 12:00 PM")
check("once summary", S.summary(sched(repeat="once", start_date="2027-04-04")),
      "Once on 04/04/2027 · 10:00 AM – 12:00 PM")
check("overnight summary",
      S.summary(sched(start_time="23:00", stop_time="01:00")),
      "Every Sun · 11:00 PM – 1:00 AM (next day)")
check("bounded summary",
      S.summary(sched(start_date="2026-10-04", end_date="2026-12-27")),
      "Every Sun · 10:00 AM – 12:00 PM, 10/04/2026 to 12/27/2026")
check("every day", S.summary(sched(days=list(range(7)))).startswith("Every day"), True)

print("\noccurrences")
wed = datetime(2026, 9, 23, 15, tzinfo=UTC)  # a Wednesday
example = [
    sched(name="Sun AM", days=[0], start_time="10:00", stop_time="12:00"),
    sched(name="Sun PM", days=[0], start_time="17:00", stop_time="19:00"),
    sched(name="Wed PM", days=[3], start_time="20:00", stop_time="21:30"),
]
nxt = S.next_window(example, wed, CT)
check("the next start from Wednesday afternoon is Wednesday 8 PM",
      (nxt.start, nxt.stop, nxt.names),
      (local(2026, 9, 23, 20), local(2026, 9, 23, 21, 30), ["Wed PM"]))
ws = S.windows(example, wed, wed + timedelta(days=7), CT)
check("one week holds all three windows",
      [w.names[0] for w in ws], ["Wed PM", "Sun AM", "Sun PM"])
check("Sunday 10 AM is 10 AM Central", ws[1].start, local(2026, 9, 27, 10))
check("inside Sunday morning",
      S.current_window(example, local(2026, 9, 27, 11), CT).names, ["Sun AM"])
check("between Sunday windows", S.current_window(example, local(2026, 9, 27, 13), CT), None)
check("stop is exclusive", S.current_window(example, local(2026, 9, 27, 12), CT), None)

check("a disabled schedule has no window",
      S.current_window([dict(example[0], enabled=False)], local(2026, 9, 27, 11), CT), None)

bounded = sched(days=[0], start_date="2026-10-04", end_date="2026-10-11")
bw = S.windows([bounded], wed, wed + timedelta(days=30), CT)
check("start and end dates bound a weekly schedule",
      [w.start for w in bw], [local(2026, 10, 4, 10), local(2026, 10, 11, 10)])

once = sched(repeat="once", start_date="2027-04-04", start_time="09:00", stop_time="11:00")
check("a one-off far ahead is still the next start",
      S.next_window([once], wed, CT).start, local(2027, 4, 4, 9))
check("a one-off in the future is not expired", S.is_expired(once, wed, CT), False)
check("a one-off in the past is expired",
      S.is_expired(sched(repeat="once", start_date="2026-09-01"), wed, CT), True)
check("a weekly schedule past its end date is expired",
      S.is_expired(sched(end_date="2026-09-13"), wed, CT), True)
check("an open-ended weekly schedule never expires", S.is_expired(sched(), wed, CT), False)

print("\novernight windows")
late = sched(days=[6], start_time="23:00", stop_time="01:00")  # Saturday night
check("an overnight window ends the next day",
      S.next_window([late], wed, CT).stop, local(2026, 9, 27, 1))
check("it is live just after midnight",
      S.current_window([late], local(2026, 9, 27, 0, 30), CT) is not None, True)
check("and over at 1 AM", S.current_window([late], local(2026, 9, 27, 1), CT), None)

print("\nDST")
dst = sched(days=[0], start_time="10:00", stop_time="12:00")
# 2026: DST starts Sun 03/08, ends Sun 11/01.
before = S.windows([dst], datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC), NYT)[0]
after = S.windows([dst], datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC), NYT)[0]
check("10 AM EDT is 14:00 UTC", before.start.hour, 14)
check("10 AM EST is 15:00 UTC", after.start.hour, 15)
check("both are 10 AM local", (before.start.astimezone(NY).hour, after.start.astimezone(NY).hour), (10, 10))
gap = S.localize(date(2026, 3, 8), time(2, 30), NY)
check("a time in the spring-forward gap moves to the next valid minute",
      gap.astimezone(NY).replace(tzinfo=None), datetime(2026, 3, 8, 3, 0))
fold = S.localize(date(2026, 11, 1), time(1, 30), NY)
check("a repeated fall-back time takes the first occurrence",
      fold, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
dup_win = S.windows([sched(days=[0], start_time="01:15", stop_time="01:45")],
                    datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC), NYT)
check("a window in the repeated hour runs once", len(dup_win), 1)
az = sched(days=[0])
check("Arizona does not shift",
      [w.start.hour for w in S.windows([az], datetime(2026, 7, 1, tzinfo=UTC),
                                        datetime(2026, 7, 8, tzinfo=UTC), PHX)]
      + [w.start.hour for w in S.windows([az], datetime(2026, 12, 1, tzinfo=UTC),
                                          datetime(2026, 12, 8, tzinfo=UTC), PHX)], [17, 17])

print("\nmerged windows")
a = sched(name="A", start_time="10:00", stop_time="12:00")
b = sched(name="B", start_time="11:00", stop_time="13:00")
c = sched(name="C", start_time="13:00", stop_time="14:00")
m = S.windows([a, b, c], local(2026, 9, 27, 0), local(2026, 9, 28, 0), CT)
check("overlapping and touching windows are one",
      [(w.start, w.stop, w.names) for w in m],
      [(local(2026, 9, 27, 10), local(2026, 9, 27, 14), ["A", "B", "C"])])


# ------------------------------------------------------------ the runner
class FakeEngine:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0
        self.fail = None
        self.pushes = 0

    async def start(self):
        if self.fail:
            raise RuntimeError(self.fail)
        self.running = True
        self.starts += 1
        return {}

    async def stop(self):
        self.running = False
        self.stops += 1
        return {}

    def _push_status(self):
        self.pushes += 1


fake = FakeEngine()
scheduler_mod.engine = fake
run = scheduler_mod.Scheduler()


def tick(when):
    asyncio.run(run.tick(when))


config.ensure_file()
put(example)
check("schedules survive a save and reload", len(config.load()["schedules"]), 3)

print("\nstarts and stops on the clock")
tick(local(2026, 9, 27, 9, 59))
check("nothing before the window", fake.running, False)
tick(local(2026, 9, 27, 10))
check("starts at 10 AM", (fake.running, fake.starts), (True, 1))
check("and says which schedule started it", run.status(local(2026, 9, 27, 10))["started_by_schedule"], "Sun AM")
check("reports when it will stop",
      run.status(local(2026, 9, 27, 10))["next_scheduled_stop"]["label"], "Sun 09/27/2026 12:00 PM CDT")
tick(local(2026, 9, 27, 11))
check("no restart mid-window", fake.starts, 1)
tick(local(2026, 9, 27, 12))
check("stops at 12 PM", (fake.running, fake.stops), (False, 1))
check("next start is the evening", run.status(local(2026, 9, 27, 12))["next_scheduled_start"]["label"],
      "Sun 09/27/2026 5:00 PM CDT")
tick(local(2026, 9, 27, 17, 0))
check("starts again at 5 PM", fake.starts, 2)
tick(local(2026, 9, 27, 19, 0))
check("stops at 7 PM", fake.running, False)

print("\nmanual override")
fake.running = False
tick(local(2026, 9, 30, 20, 1))
check("Wednesday 8 PM starts", fake.running, True)
run.manual_stop(local(2026, 9, 30, 20, 30))
asyncio.run(fake.stop())
tick(local(2026, 9, 30, 20, 31))
check("a manual stop mid-window holds it", fake.running, False)
check("the hold is reported", run.status(local(2026, 9, 30, 20, 31))["held"], True)
run.manual_start()
asyncio.run(fake.start())
tick(local(2026, 9, 30, 20, 40))
check("a manual start in the window is adopted...", run.owned_until, local(2026, 9, 30, 21, 30))
tick(local(2026, 9, 30, 21, 30))
check("...and stopped at the window's end", fake.running, False)

run.manual_start()
asyncio.run(fake.start())
tick(local(2026, 10, 1, 9))
tick(local(2026, 10, 1, 15))
check("a manual start outside any window is left running", fake.running, True)
asyncio.run(fake.stop())
run.manual_stop(local(2026, 10, 1, 15))

print("\nalready running when a window opens")
asyncio.run(fake.start())
starts = fake.starts
tick(local(2026, 10, 4, 10))
check("not restarted", fake.starts, starts)
tick(local(2026, 10, 4, 12))
check("stopped at the window's end", fake.running, False)

print("\ncatch-up and failures")
fresh = scheduler_mod.Scheduler()
asyncio.run(fresh.tick(local(2026, 10, 4, 11)))
check("a restart mid-window starts the session", fake.running, True)
asyncio.run(fake.stop())
fresh2 = scheduler_mod.Scheduler()
starts = fake.starts
asyncio.run(fresh2.tick(local(2026, 10, 4, 15)))
check("a missed window is not run afterwards", fake.starts, starts)

fake.fail = "No OpenAI API key set."
fresh3 = scheduler_mod.Scheduler()
asyncio.run(fresh3.tick(local(2026, 10, 4, 10)))
check("a failed start leaves it stopped", fake.running, False)
fake.fail = None
asyncio.run(fresh3.tick(local(2026, 10, 4, 10, 1)))
check("and is retried while the window is open", fake.running, True)

print("\nschedule changes under a live session")
put([])
asyncio.run(fresh3.tick(local(2026, 10, 4, 10, 2)))
check("deleting the schedule does not cut the session off", fake.running, True)
asyncio.run(fresh3.tick(local(2026, 10, 4, 12, 5)))
check("and it is no longer the scheduler's to stop", fake.running, True)
asyncio.run(fake.stop())

print("\noverlapping windows")
put([a, b])
r = scheduler_mod.Scheduler()
starts0, stops0 = fake.starts, fake.stops
for h, mi in ((10, 0), (11, 0), (11, 59), (12, 0), (12, 30)):
    asyncio.run(r.tick(local(2026, 10, 4, h, mi)))
check("one start, no stop across the overlap",
      (fake.starts - starts0, fake.stops - stops0, fake.running), (1, 0, True))
asyncio.run(r.tick(local(2026, 10, 4, 13)))
check("stopped at the later end", fake.running, False)


# ------------------------------------------------------------ plan times, refs and the config
print("\nplan times")
plan_day = [(local(2026, 9, 27, 9, 0), local(2026, 9, 27, 9, 0)), (local(2026, 9, 27, 11, 0), local(2026, 9, 27, 11, 0))]


def fake_plan(frm, to):
    return [p for p in plan_day if frm <= p[0] < to]


withplan = S.validate({"name": "Services", "windows": [], "plan_times": True, "plan_before": 30, "plan_after": 60})
check("a plan time opens a window around it",
      [(w.start, w.stop) for w in S.windows([withplan], local(2026, 9, 27, 0), local(2026, 9, 28, 0), CT, fake_plan)],
      [(local(2026, 9, 27, 8, 30), local(2026, 9, 27, 10, 0)), (local(2026, 9, 27, 10, 30), local(2026, 9, 27, 12, 0))])
check("with no plan source there are no windows",
      S.windows([withplan], local(2026, 9, 27, 0), local(2026, 9, 28, 0), CT), [])
check("is_open follows the plan", (S.is_open(withplan, local(2026, 9, 27, 9, 30), CT, fake_plan),
                                   S.is_open(withplan, local(2026, 9, 27, 10, 15), CT, fake_plan)), (True, False))
check("a plan-times schedule never expires", S.is_expired(withplan, wed, CT), False)
check("the summary names the plan times", "Planning Center" in S.summary(withplan), True)
check("is_open_local reads a naive Relay-zone time",
      (S.is_open_local(example[0], datetime(2026, 9, 27, 11, 0), CT), S.is_open_local(example[0], datetime(2026, 9, 27, 13, 0), CT)),
      (True, False))

print("\nPlanning Center supplies the plan times")
from app.modules.planning_center import MODULE as PC
from app.modules.planning_center.pclive import Track, follower
tr = Track("7")
tr.plan = {"id": "700", "times": [{"id": "t1", "type": "service", "starts_at": plan_day[0][0].timestamp()},
                                  {"id": "r1", "type": "rehearsal", "starts_at": plan_day[1][0].timestamp()}]}
follower.tracks["7"] = tr
check("only the services in the range, as UTC pairs",
      PC._service_times(local(2026, 9, 27, 0), local(2026, 9, 28, 0)), [(plan_day[0][0], plan_day[0][0])])
check("and none outside it", PC._service_times(local(2026, 9, 28, 0), local(2026, 9, 29, 0)), [])
follower.tracks.pop("7")

print("\nrefs")
check("a ref names a schedule", S.ref("abc12345"), "schedule:relay:abc12345")
check("and reads back", S.ref_id("schedule:relay:abc12345"), "abc12345")
check("anything else is not a ref", (S.ref_id("abc"), S.ref_id("schedule:relay:"), S.ref_id(None)), (None, None, None))
check("find takes a ref or a bare id", (S.find(example, S.ref(example[1]["id"]))["name"], S.find(example, example[2]["id"])["name"],
                                        S.find(example, "schedule:relay:nope")), ("Sun PM", "Wed PM", None))

print("\nconfig: the one time zone and the engine's schedules")
config.save({"timezone": "America/Denver"})
check("the Relay zone is kept", config.timezone(), "America/Denver")
config.save({"timezone": "Asia/Tokyo"})
check("a zone outside the US list falls back", config.timezone() in S.TZ_LABELS, True)
put(example)
check("each picked schedule is in the engine's list", len(config.load()["engine_schedules"]), 3)
config.save({"engine_schedules": [S.ref(example[0]["id"]), "schedule:relay:gone"]})
check("a ref to a deleted schedule is dropped", config.get()["engine_schedules"], [S.ref(example[0]["id"])])
check("and the engine drives only the picked ones", [x["name"] for x in scheduler_mod.engine_schedules()], ["Sun AM"])

Path(config.CONFIG_PATH).write_text(json.dumps({"signage": {"timezone": "America/Los_Angeles"}}))
check("with no schedule to take it from, the signage zone becomes Relay's", config.load()["timezone"], "America/Los_Angeles")

# A config from before #526: flat schedules each with a zone, and no engine list or Relay zone.
old = {"schedules": [{"id": "old1", "name": "Sunday", "enabled": True, "repeat": "weekly", "days": [0],
                      "start_date": None, "end_date": None, "start_time": "10:00", "stop_time": "12:00",
                      "timezone": "America/Chicago"}]}
Path(config.CONFIG_PATH).write_text(json.dumps(old))
cfg = config.load()
check("an older config keeps its schedule as a named one with a window",
      (cfg["schedules"][0]["name"], cfg["schedules"][0]["windows"][0]["start_time"]), ("Sunday", "10:00"))
check("the engine keeps driving every schedule it did", cfg["engine_schedules"], ["schedule:relay:old1"])
check("the Relay zone starts from the schedule's own", cfg["timezone"], "America/Chicago")


# ------------------------------------------------------------ the admin API
print("\nadmin API")
from fastapi.testclient import TestClient
from app import engine as engine_mod
from app.main import app

config.save({"schedules": [], "engine_schedules": [], "timezone": CT, "port": 8000, "admin_port": 8000})
body = {"name": "Sunday morning", "windows": [{"repeat": "weekly", "days": [0], "start_time": "10:00", "stop_time": "12:00"}],
        "engine": True}
cl = _db.client(app, base_url="http://testserver")
with cl:
    scheduler_mod.engine = engine_mod.engine
    check("needs a sign-in", TestClient(app).get("/api/admin/schedules").status_code, 401)
    got = cl.get("/api/admin/schedules").json()
    check("lists US zones only", [z["id"] for z in got["timezones"]], [z for z, _ in S.US_TIMEZONES])
    check("and says which zone Relay is in", got["timezone"], CT)
    res = cl.post("/api/admin/schedules", json=body)
    check("create", res.status_code, 200)
    item = res.json()["schedules"][0]
    check("the listing carries the US summary", item["summary"], "Every Sun · 10:00 AM – 12:00 PM")
    check("and the next start in US format", item["next_start"].endswith("10:00 AM CDT")
          or item["next_start"].endswith("10:00 AM CST"), True)
    check("it is picked for the engine", (item["engine"], item["ref"]), (True, S.ref(item["id"])))
    check("and the engine is listed as using it", item["used_by"], [{"where": "Engine", "what": "Starts and stops live captions"}])
    res = cl.post("/api/admin/schedules", json=dict(body, name=""))
    check("a schedule with no name is refused", (res.status_code, "name" in res.json()["error"]), (400, True))
    res = cl.post("/api/admin/schedules", json=dict(body, windows=[]))
    check("a schedule with nothing in it is refused", (res.status_code, "window" in res.json()["error"]), (400, True))
    res = cl.put(f"/api/admin/schedules/{item['id']}",
                 json=dict(body, windows=[dict(body["windows"][0], days=[0, 3])], enabled=False, engine=False))
    got = res.json()["schedules"][0]
    check("update", (res.status_code, got["windows"][0]["days"], got["enabled"], got["engine"]), (200, [0, 3], False, False))
    check("the id is kept on update", got["id"], item["id"])
    check("update of an unknown id", cl.put("/api/admin/schedules/nope", json=body).status_code, 404)
    check("status carries the schedule block", "next_scheduled_start" in cl.get("/api/admin/status").json()["schedule"], True)
    check("stored in config.json", config.load()["schedules"][0]["windows"][0]["days"], [0, 3])

    print("\n  the Relay zone, set under System")
    res = cl.post("/api/admin/config", json={"timezone": "America/Denver"})
    check("the zone is saved", (res.status_code, config.timezone()), (200, "America/Denver"))
    res = cl.post("/api/admin/config", json={"timezone": "Asia/Tokyo"})
    check("a zone outside the US list is refused", (res.status_code, config.timezone()), (400, "America/Denver"))
    check("the state carries the zone list", [z["id"] for z in cl.get("/api/admin/state").json()["timezones"]],
          [z for z, _ in S.US_TIMEZONES])

    print("\n  used by, and delete")
    cl.post("/api/admin/config", json={"timezone": CT})
    ref = S.ref(item["id"])
    board = {"id": "lobby", "name": "Lobby", "pages": [{"id": "p1", "name": "Welcome", "widgets": []}],
             "rotation": {"mode": "schedule", "schedule": [{"days": "all", "from": "09:00", "to": "10:00", "schedule": ref, "show": "p1"}]}}
    config.save({"boards": [board]})
    got = cl.get("/api/admin/schedules").json()["schedules"][0]
    check("a board row that names it is listed", got["used_by"], [{"where": "Boards", "what": "Lobby: schedule row 1"}])
    res = cl.delete(f"/api/admin/schedules/{item['id']}")
    check("deleting one in use is refused, with where", (res.status_code, "still used" in res.json()["error"],
                                                         res.json()["used_by"]), (409, True, got["used_by"]))
    check("and it is still there", len(config.load()["schedules"]), 1)
    config.save({"boards": []})
    check("delete", cl.delete(f"/api/admin/schedules/{item['id']}").json()["schedules"], [])
    check("delete of an unknown id", cl.delete("/api/admin/schedules/nope").status_code, 404)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("schedules: all checks passed")
