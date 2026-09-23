"""Schedule tests: validation, occurrences, DST, overnight and merged windows,
US formatting, the start/stop rules, and the admin API.

Does not contact OpenAI or open an audio device -- the scheduler drives a fake
engine. Run: .venv/bin/python tests/test_schedules.py
"""
import asyncio, os, sys, tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from app import config, schedules as S
from app import scheduler as scheduler_mod

UTC = timezone.utc
CHI = ZoneInfo("America/Chicago")
NY = ZoneInfo("America/New_York")

fails = []
def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def raises(label, fn, fragment):
    try:
        fn()
    except ValueError as exc:
        check(label, fragment in str(exc), True)
        return
    check(label + " (did not raise)", False, True)


def sched(**kw):
    base = {"repeat": "weekly", "days": [0], "start_time": "10:00",
            "stop_time": "12:00", "timezone": "America/Chicago"}
    base.update(kw)
    return S.validate(base)


def local(y, mo, d, h, mi=0, tz=CHI):
    return datetime(y, mo, d, h, mi, tzinfo=tz).astimezone(UTC)


print("validation")
s = sched(name="  Sunday   morning ")
check("name is trimmed and squeezed", s["name"], "Sunday morning")
check("a new schedule is enabled", s["enabled"], True)
check("an id is issued", len(s["id"]) > 0, True)
check("days are deduped and sorted", sched(days=[3, 0, 3])["days"], [0, 3])
raises("only US zones", lambda: sched(timezone="Europe/London"), "US time zone")
raises("weekly needs a day", lambda: sched(days=[]), "day of the week")
raises("days stay in 0-6", lambda: sched(days=[7]), "0 (Sunday)")
raises("start and stop differ", lambda: sched(stop_time="10:00"), "must differ")
raises("24-hour times only on the wire", lambda: sched(start_time="10:00 AM"), "Start time")
raises("once needs a date", lambda: sched(repeat="once"), "needs a date")
raises("end before start", lambda: sched(start_date="2026-10-10", end_date="2026-10-01"), "before")
raises("bad date", lambda: sched(start_date="10/04/2026"), "valid date")
o = sched(repeat="once", start_date="2027-04-04", days=[1, 2], end_date="2027-05-01")
check("once drops days and end date", (o["days"], o["end_date"]), ([], None))

print("\nconfig load is tolerant")
cleaned = S.clean_list([
    {"repeat": "weekly", "days": [0], "start_time": "10:00", "stop_time": "12:00",
     "timezone": "America/Chicago"},
    {"repeat": "weekly", "days": [], "start_time": "10:00", "stop_time": "12:00",
     "timezone": "America/Chicago"},
    "not a schedule",
])
check("an invalid entry is dropped, not fatal", len(cleaned), 1)
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
      "Every Sun, Wed · 10:00 AM – 12:00 PM Central")
check("once summary", S.summary(sched(repeat="once", start_date="2027-04-04",
                                       timezone="America/New_York")),
      "Once on 04/04/2027 · 10:00 AM – 12:00 PM Eastern")
check("overnight summary",
      S.summary(sched(start_time="23:00", stop_time="01:00", timezone="America/Phoenix")),
      "Every Sun · 11:00 PM – 1:00 AM (next day) Arizona")
check("bounded summary",
      S.summary(sched(start_date="2026-10-04", end_date="2026-12-27")),
      "Every Sun · 10:00 AM – 12:00 PM Central, 10/04/2026 to 12/27/2026")
check("every day", S.summary(sched(days=list(range(7)))).startswith("Every day"), True)

print("\noccurrences")
wed = datetime(2026, 9, 23, 15, tzinfo=UTC)  # a Wednesday
example = [
    sched(name="Sun AM", days=[0], start_time="10:00", stop_time="12:00"),
    sched(name="Sun PM", days=[0], start_time="17:00", stop_time="19:00"),
    sched(name="Wed PM", days=[3], start_time="20:00", stop_time="21:30"),
]
nxt = S.next_window(example, wed)
check("the next start from Wednesday afternoon is Wednesday 8 PM",
      (nxt.start, nxt.stop, nxt.names),
      (local(2026, 9, 23, 20), local(2026, 9, 23, 21, 30), ["Wed PM"]))
ws = S.windows(example, wed, wed + timedelta(days=7))
check("one week holds all three windows",
      [w.names[0] for w in ws], ["Wed PM", "Sun AM", "Sun PM"])
check("Sunday 10 AM is 10 AM Central", ws[1].start, local(2026, 9, 27, 10))
check("inside Sunday morning",
      S.current_window(example, local(2026, 9, 27, 11)).names, ["Sun AM"])
check("between Sunday windows", S.current_window(example, local(2026, 9, 27, 13)), None)
check("stop is exclusive", S.current_window(example, local(2026, 9, 27, 12)), None)

check("a disabled schedule has no window",
      S.current_window([dict(example[0], enabled=False)], local(2026, 9, 27, 11)), None)

bounded = sched(days=[0], start_date="2026-10-04", end_date="2026-10-11")
bw = S.windows([bounded], wed, wed + timedelta(days=30))
check("start and end dates bound a weekly schedule",
      [w.start for w in bw], [local(2026, 10, 4, 10), local(2026, 10, 11, 10)])

once = sched(repeat="once", start_date="2027-04-04", start_time="09:00", stop_time="11:00")
check("a one-off far ahead is still the next start",
      S.next_window([once], wed).start, local(2027, 4, 4, 9))
check("a one-off in the future is not expired", S.is_expired(once, wed), False)
check("a one-off in the past is expired",
      S.is_expired(sched(repeat="once", start_date="2026-09-01"), wed), True)
check("a weekly schedule past its end date is expired",
      S.is_expired(sched(end_date="2026-09-13"), wed), True)
check("an open-ended weekly schedule never expires", S.is_expired(sched(), wed), False)

print("\novernight windows")
late = sched(days=[6], start_time="23:00", stop_time="01:00")  # Saturday night
check("an overnight window ends the next day",
      S.next_window([late], wed).stop, local(2026, 9, 27, 1))
check("it is live just after midnight",
      S.current_window([late], local(2026, 9, 27, 0, 30)) is not None, True)
check("and over at 1 AM", S.current_window([late], local(2026, 9, 27, 1)), None)

print("\nDST")
dst = sched(days=[0], start_time="10:00", stop_time="12:00", timezone="America/New_York")
# 2026: DST starts Sun 03/08, ends Sun 11/01.
before = S.windows([dst], datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC))[0]
after = S.windows([dst], datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))[0]
check("10 AM EDT is 14:00 UTC", before.start.hour, 14)
check("10 AM EST is 15:00 UTC", after.start.hour, 15)
check("both are 10 AM local", (before.start.astimezone(NY).hour, after.start.astimezone(NY).hour), (10, 10))
gap = S.localize(date(2026, 3, 8), time(2, 30), NY)
check("a time in the spring-forward gap moves to the next valid minute",
      gap.astimezone(NY).replace(tzinfo=None), datetime(2026, 3, 8, 3, 0))
fold = S.localize(date(2026, 11, 1), time(1, 30), NY)
check("a repeated fall-back time takes the first occurrence",
      fold, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
dup_win = S.windows([sched(days=[0], start_time="01:15", stop_time="01:45",
                           timezone="America/New_York")],
                    datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))
check("a window in the repeated hour runs once", len(dup_win), 1)
az = sched(days=[0], timezone="America/Phoenix")
check("Arizona does not shift",
      [w.start.hour for w in S.windows([az], datetime(2026, 7, 1, tzinfo=UTC),
                                        datetime(2026, 7, 8, tzinfo=UTC))]
      + [w.start.hour for w in S.windows([az], datetime(2026, 12, 1, tzinfo=UTC),
                                          datetime(2026, 12, 8, tzinfo=UTC))], [17, 17])

print("\nmerged windows")
a = sched(name="A", start_time="10:00", stop_time="12:00")
b = sched(name="B", start_time="11:00", stop_time="13:00")
c = sched(name="C", start_time="13:00", stop_time="14:00")
m = S.windows([a, b, c], local(2026, 9, 27, 0), local(2026, 9, 28, 0))
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
config.save({"schedules": example})
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
config.save({"schedules": []})
asyncio.run(fresh3.tick(local(2026, 10, 4, 10, 2)))
check("deleting the schedule does not cut the session off", fake.running, True)
asyncio.run(fresh3.tick(local(2026, 10, 4, 12, 5)))
check("and it is no longer the scheduler's to stop", fake.running, True)
asyncio.run(fake.stop())

print("\noverlapping windows")
config.save({"schedules": [a, b]})
r = scheduler_mod.Scheduler()
starts0, stops0 = fake.starts, fake.stops
for h, mi in ((10, 0), (11, 0), (11, 59), (12, 0), (12, 30)):
    asyncio.run(r.tick(local(2026, 10, 4, h, mi)))
check("one start, no stop across the overlap",
      (fake.starts - starts0, fake.stops - stops0, fake.running), (1, 0, True))
asyncio.run(r.tick(local(2026, 10, 4, 13)))
check("stopped at the later end", fake.running, False)


# ------------------------------------------------------------ the admin API
print("\nadmin API")
from fastapi.testclient import TestClient
from app import engine as engine_mod
from app.main import app

config.save({"admin_token": "t0ken", "schedules": [], "port": 8000, "admin_port": 8000})
H = {"x-admin-token": "t0ken"}
body = {"name": "Sunday morning", "repeat": "weekly", "days": [0],
        "start_time": "10:00", "stop_time": "12:00", "timezone": "America/Chicago"}
with TestClient(app) as cl:
    scheduler_mod.engine = engine_mod.engine
    check("needs a token", cl.get("/api/admin/schedules").status_code, 401)
    got = cl.get("/api/admin/schedules", headers=H).json()
    check("lists US zones only", [z["id"] for z in got["timezones"]], [z for z, _ in S.US_TIMEZONES])
    res = cl.post("/api/admin/schedules", headers=H, json=body)
    check("create", res.status_code, 200)
    item = res.json()["schedules"][0]
    check("the listing carries the US summary", item["summary"],
          "Every Sun · 10:00 AM – 12:00 PM Central")
    check("and the next start in US format", item["next_start"].endswith("10:00 AM CDT")
          or item["next_start"].endswith("10:00 AM CST"), True)
    res = cl.post("/api/admin/schedules", headers=H, json=dict(body, timezone="Asia/Tokyo"))
    check("a non-US zone is refused", (res.status_code, "US time zone" in res.json()["error"]),
          (400, True))
    res = cl.put(f"/api/admin/schedules/{item['id']}", headers=H,
                 json=dict(body, days=[0, 3], enabled=False))
    check("update", (res.status_code, res.json()["schedules"][0]["days"],
                     res.json()["schedules"][0]["enabled"]), (200, [0, 3], False))
    check("the id is kept on update", res.json()["schedules"][0]["id"], item["id"])
    check("update of an unknown id", cl.put("/api/admin/schedules/nope", headers=H,
                                            json=body).status_code, 404)
    check("status carries the schedule block",
          "next_scheduled_start" in cl.get("/api/admin/status", headers=H).json()["schedule"], True)
    check("stored in config.json", config.load()["schedules"][0]["days"], [0, 3])
    check("delete", cl.delete(f"/api/admin/schedules/{item['id']}", headers=H).json()["schedules"], [])
    check("delete of an unknown id", cl.delete("/api/admin/schedules/nope", headers=H).status_code, 404)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("schedules: all checks passed")
