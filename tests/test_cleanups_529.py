"""Shared concepts 8 (#529): remote intercom sessions are rows of intercom_sessions, crew times are ISO UTC text, and
a crew "page" is a crew notification. The migrations are run on old-shaped data, so history reads the same after.
Run: python tests/test_cleanups_529.py"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import db  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"


def apply(conn: sqlite3.Connection, folder: Path, upto: str = "9999", after: str = "") -> None:
    for path in sorted(folder.glob("*.sql")):
        if after < path.name[:4] <= upto:
            for stmt in db._statements(path.read_text(encoding="utf-8")):
                conn.execute(stmt)


def one(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


with section("remote intercom sessions move into intercom_sessions"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.executescript("create table users (id integer primary key); insert into users values (1), (2);")
    folder = APP / "modules" / "intercom" / "migrations"
    apply(conn, folder, upto="0001")
    remote = APP / "modules" / "intercom_remote" / "migrations"
    apply(conn, remote, upto="0001")
    conn.executescript("""
        insert into intercom_sessions (user_id, via, started_at, ended_at, end_reason)
            values (1, 'local', '2026-03-01T10:00:00Z', '2026-03-01T10:05:00Z', 'left');
        insert into intercom_remote_sessions (device_id, user_id, started_at, ended_at, end_reason) values
            (null, 2, '2026-03-02T09:00:00Z', '2026-03-02T09:30:00Z', 'signed_out'),
            (null, 1, '2026-03-03T09:00:00Z', null, null);
    """)
    apply(conn, remote, after="0001")
    eq("the table is gone", one(conn, "select name from sqlite_master where name = 'intercom_remote_sessions'"), [])
    rows = one(conn, "select user_id, via, started_at, ended_at, end_reason from intercom_sessions order by id")
    eq("the local one is untouched, the remote ones follow it with via = remote",
       [(r["user_id"], r["via"], r["started_at"], r["ended_at"], r["end_reason"]) for r in rows],
       [(1, "local", "2026-03-01T10:00:00Z", "2026-03-01T10:05:00Z", "left"),
        (2, "remote", "2026-03-02T09:00:00Z", "2026-03-02T09:30:00Z", "signed_out"),
        (1, "remote", "2026-03-03T09:00:00Z", None, None)])
    conn.close()

with section("crew epoch columns become ISO UTC text and pages become notifications"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    apply(conn, APP / "migrations")                  # core: users, devices, person_links
    conn.execute("pragma foreign_keys = on")
    for n in (1, 2):
        conn.execute("insert into users (id, username, name, password_hash, role, created_at) values (?, ?, ?, 'x', 'member', '')",
                     (n, f"u{n}", f"U{n}"))
    crew = APP / "modules" / "crew" / "migrations"
    apply(conn, crew, upto="0013")
    T = 1_800_000_000          # 2027-01-15T08:00:00Z
    conn.executescript(f"""
        insert into crew_pages (id, message, sent_by, sent_ts, created_at) values (1, 'Camera 1 up', 1, {T}.4, '2027-01-15T08:00:00Z');
        insert into crew_page_recipients (page_id, user_id, token, pushes, delivered, confirmed_at)
            values (1, 1, 'tok-a', 3, 2, '2027-01-15T08:00:09Z'), (1, 2, 'tok-b', 1, 0, null);
        insert into crew_calltimes (id, team, kind, message, created_at) values (1, 'Camera', 'before', 'm', '');
        insert into crew_calltime_runs (rule_id, occasion, fired_ts, page_id, people) values (1, 'a', {T + 60}, 1, 2),
            (1, 'b', {T + 120}, null, 0);
        insert into crew_chat_messages (channel, user_id, author, body, created_ts) values ('team', 1, 'Ann', 'hi', {T + 5});
        insert into crew_checklists (id, title) values (1, 'Setup');
        insert into crew_checklist_items (id, checklist_id, sort, text) values (1, 1, 0, 'Cables');
        insert into crew_checklist_done (plan_id, item_id, user_id, done_ts) values ('700', 1, 1, {T + 30});
        insert into crew_arrivals (plan_id, user_id, arrived_ts, method) values ('700', 1, {T + 90}, 'wifi');
        insert into crew_devices (id, user_id, kind, value, label, added_ts, added_by) values (1, 1, 'mac', 'aa', 'Phone', {T}, 'self');
        insert into crew_resources (id, position, kind, title, url, created_by, created_ts, position_ref)
            values (1, 'Camera', 'link', 'Guide', 'https://x.test', 1, {T + 1}, 'position:pco:1/2');
    """)
    apply(conn, crew, after="0013")
    names = {r["name"] for r in conn.execute("select name from sqlite_master where type = 'table'")}
    check("the page tables are gone", not names & {"crew_pages", "crew_page_recipients"})
    check("the notification tables are there", {"crew_notifications", "crew_notification_recipients"} <= names)
    iso = lambda s: db.iso(T + s)                                  # noqa: E731
    eq("a notification keeps its message, sender and (one) send time",
       one(conn, "select id, message, sent_by, created_at from crew_notifications"),
       [{"id": 1, "message": "Camera 1 up", "sent_by": 1, "created_at": iso(0)}])
    eq("its receipts keep their state",
       one(conn, "select notification_id, user_id, token, pushes, delivered, confirmed_at from crew_notification_recipients order by user_id"),
       [{"notification_id": 1, "user_id": 1, "token": "tok-a", "pushes": 3, "delivered": 2, "confirmed_at": "2027-01-15T08:00:09Z"},
        {"notification_id": 1, "user_id": 2, "token": "tok-b", "pushes": 1, "delivered": 0, "confirmed_at": None}])
    eq("call-time runs keep the notification they sent",
       one(conn, "select occasion, fired_at, notification_id, people from crew_calltime_runs order by occasion"),
       [{"occasion": "a", "fired_at": iso(60), "notification_id": 1, "people": 2},
        {"occasion": "b", "fired_at": iso(120), "notification_id": None, "people": 0}])
    eq("chat", one(conn, "select author, body, created_at from crew_chat_messages"),
       [{"author": "Ann", "body": "hi", "created_at": iso(5)}])
    eq("a checklist tick", one(conn, "select plan_id, item_id, user_id, done_at from crew_checklist_done"),
       [{"plan_id": "700", "item_id": 1, "user_id": 1, "done_at": iso(30)}])
    eq("an arrival", one(conn, "select plan_id, user_id, arrived_at, method from crew_arrivals"),
       [{"plan_id": "700", "user_id": 1, "arrived_at": iso(90), "method": "wifi"}])
    eq("a check-in device", one(conn, "select value, label, added_at, added_by from crew_devices"),
       [{"value": "aa", "label": "Phone", "added_at": iso(0), "added_by": "self"}])
    eq("a resource keeps its position ref", one(conn, "select title, created_at, position_ref from crew_resources"),
       [{"title": "Guide", "created_at": iso(1), "position_ref": "position:pco:1/2"}])
    for table in ("crew_notifications", "crew_calltime_runs", "crew_chat_messages", "crew_checklist_done", "crew_arrivals",
                  "crew_devices", "crew_resources"):
        cols = [r["name"] for r in conn.execute(f"pragma table_info({table})")]
        check(f"{table} has no epoch column", not [c for c in cols if c.endswith("_ts")])
    eq("the dropped parent did not cascade onto the run", one(conn, "select count(*) as n from crew_calltime_runs")[0]["n"], 2)
    eq("foreign keys still hold", one(conn, "pragma foreign_key_check"), [])
    conn.close()

with section("epoch and iso round-trip"):
    eq("iso", db.iso(1_800_000_000), "2027-01-15T08:00:00Z")
    eq("epoch", db.epoch("2027-01-15T08:00:00Z"), 1_800_000_000.0)
    eq("none stays none", db.epoch(None), None)

done()
