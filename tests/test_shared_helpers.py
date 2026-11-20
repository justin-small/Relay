"""app/text.py and app/timewin.py (#522): the shared name, slug, weekday and clock helpers, and the call-time
migration that moved saved days to Sunday = 0."""
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import boards, text, timewin  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

SUNDAY = date(2026, 11, 15)
CHICAGO = ZoneInfo("America/Chicago")

with section("names"):
    eq("squeeze", text.squeeze("  Main \t  Stage\n"), "Main Stage")
    eq("squeeze of None", text.squeeze(None), "")
    eq("fold", text.fold(" Worship  LEADER "), "worship leader")
    check("same name ignores case and spacing", text.same_name("Sound  Tech", " sound tech"))
    check("different names differ", not text.same_name("Sound", "Lights"))
    check("two blanks are not a match", not text.same_name("", "  "))
    eq("accents stripped, case kept", text.strip_accents("José Ñúñez"), "Jose Nunez")
    eq("plain", text.plain("O'Brien-Smith,  José"), "o brien smith jose")
    eq("plain folds ß", text.plain("Strauß"), "strauss")

with section("slugs"):
    eq("words joined by hyphens", text.slug("Main Lobby TV!"), "main-lobby-tv")
    eq("numbered when taken", text.slug("Lobby", {"lobby", "lobby-2"}), "lobby-3")
    eq("fallback", text.slug("!!!", fallback="board"), "board")
    eq("limit trims a trailing hyphen", text.slug("abcde fgh", limit=6), "abcde")
    eq("underscore separator", text.slug("Band Ready", sep="_", limit=None), "band_ready")
    eq("no limit", len(text.slug("x" * 80, limit=None)), 80)

with section("weekdays count from Sunday"):
    eq("Sunday is 0", timewin.weekday(SUNDAY), 0)
    eq("Saturday is 6", timewin.weekday(date(2026, 11, 21)), 6)
    eq("a datetime works too", timewin.weekday(datetime(2026, 11, 16, 9, 0)), 1)
    eq("Monday-first renumbered", [timewin.from_monday_first(d) for d in range(7)], [1, 2, 3, 4, 5, 6, 0])
    eq("names line up", (timewin.DAY_NAMES[0], timewin.DAY_ABBR[6], timewin.DAY_KEYS[1]), ("Sunday", "Sat", "mon"))

with section("clock times"):
    check("09:30 is a clock", timewin.is_clock("09:30"))
    check("9:30 is not, strictly", not timewin.is_clock("9:30"))
    check("24:00 is not", not timewin.is_clock("24:00"))
    check("a number is not", not timewin.is_clock(930))
    eq("loose pads the hour", timewin.clean_clock(" 9:30 ", loose=True), "09:30")
    eq("strict refuses one digit", timewin.clean_clock("9:30"), None)
    eq("minutes", timewin.minutes("13:45"), 13 * 60 + 45)
    eq("12-hour", [timewin.us_clock(t) for t in ("00:05", "12:00", "20:30")], ["12:05 AM", "12:00 PM", "8:30 PM"])

with section("daily windows"):
    every = lambda d: True  # noqa: E731
    at = datetime(2026, 11, 15, 10, 0)
    eq("inside a morning window", timewin.window_start(at, 9 * 60, 11 * 60, every), datetime(2026, 11, 15, 9, 0))
    eq("outside it", timewin.window_start(at, 11 * 60, 12 * 60, every), None)
    eq("whole day when start equals end", timewin.window_start(at, 0, 0, every), datetime(2026, 11, 15, 0, 0))
    late = datetime(2026, 11, 16, 1, 0)
    eq("past midnight, started yesterday", timewin.window_start(late, 22 * 60, 2 * 60, every), datetime(2026, 11, 15, 22, 0))
    sundays = lambda d: timewin.weekday(d) == 0  # noqa: E731
    eq("the start day is the one checked", timewin.window_start(late, 22 * 60, 2 * 60, sundays), datetime(2026, 11, 15, 22, 0))
    eq("a Monday window is not open on Sunday", timewin.window_start(at, 9 * 60, 11 * 60, lambda d: timewin.weekday(d) == 1), None)
    aware = datetime(2026, 11, 1, 10, 0, tzinfo=CHICAGO)          # the day clocks went back
    began = timewin.window_start(aware, 9 * 60, 11 * 60, every)
    eq("wall-clock start kept across daylight saving", (began.hour, began.minute, began.tzinfo), (9, 0, CHICAGO))

with section("board schedules count from Sunday"):
    def board(days: str, frm: str = "00:00", to: str = "00:00") -> dict:
        return {"rotation": {"schedule": [{"days": days, "from": frm, "to": to}]}}
    sun, sat, mon = datetime(2026, 11, 15, 10), datetime(2026, 11, 21, 10), datetime(2026, 11, 16, 10)
    eq("sun on a Sunday", boards.schedule_row(board("sun"), sun), 0)
    eq("sun on a Monday", boards.schedule_row(board("sun"), mon), None)
    eq("weekdays on Saturday", boards.schedule_row(board("weekdays"), sat), None)
    eq("weekdays on Monday", boards.schedule_row(board("weekdays"), mon), 0)
    eq("weekends on Sunday", boards.schedule_row(board("weekends"), sun), 0)
    eq("Saturday night past midnight is still Saturday's row", boards.schedule_row(
        board("sat", "22:00", "02:00"), datetime(2026, 11, 22, 1, 0)), 0)

with section("saved call times move to Sunday = 0"):
    migrations = ROOT / "app" / "modules" / "crew" / "migrations"
    con = sqlite3.connect(":memory:")
    con.execute("create table crew_pages (id integer primary key)")
    con.executescript((migrations / "0003_calltimes.sql").read_text())
    for weekday in (0, 5, 6, None):                                # Monday, Saturday, Sunday (from Monday), "before"
        con.execute("insert into crew_calltimes (team, kind, weekday, message, created_at) values ('T', 'fixed', ?, 'm', '')",
                    (weekday,))
    con.executescript((migrations / "0009_calltime_sunday_first.sql").read_text())
    eq("Monday 1, Saturday 6, Sunday 0, none kept", [r[0] for r in con.execute(
        "select weekday from crew_calltimes order by id")], [1, 6, 0, None])
    con.close()

done()
