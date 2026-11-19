"""Devices (#528): one core device per phone, with sign-in trust, intercom, check-in and push as parts; forgetting the
phone ends every part. Run: python tests/test_devices.py"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config, db, devices, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402
from app.modules.crew import devices as crew_devices, notifications, webpush  # noqa: E402
from app.modules.intercom import devices as intercom_devices  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "intercom": {"enabled": True}}})
modules.attach("crew")
modules.attach("intercom")
owner = _db.owner()


def subscription(tag: str) -> dict:
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{tag}",
            "keys": {"p256dh": webpush.b64e(b"\x04" + bytes(64)), "auth": webpush.b64e(bytes(16))}}


def parts_of(row: dict) -> set[str]:
    return {p["kind"] for p in row["parts"]}


with section("refs"):
    eq("ref", devices.ref(7), "device:relay:7")
    eq("round trip", devices.device_id("device:relay:7"), 7)
    eq("another provider is not a relay device", devices.device_id("device:pco:7"), None)

with section("parts of one phone join by user and user agent"):
    cred, row = intercom_devices.create(owner.id, name="", user_agent=IPHONE)
    core = row["device_id"]
    check("the intercom credential is a part of a core device", core is not None)
    crew_devices.add(owner.id, "mac", "3c:22:fb:5e:90:40", "", "self", IPHONE)
    notifications.subscribe(owner.id, subscription("a"), IPHONE)
    listed = devices.listing(owner.id)
    eq("one device holds the intercom, check-in and push parts", [parts_of(d) for d in listed],
       [{"intercom", "crew_checkin", "crew_push"}])
    eq("named from the user agent", listed[0]["name"], "iPhone")
    eq("a platform", listed[0]["platform"], "ios")
    token, trusted = users.trust_device("Booth iPad", owner, IPHONE)
    eq("sign-in trust joins it too", [parts_of(d) for d in devices.listing(owner.id)],
       [{"signin", "intercom", "crew_checkin", "crew_push"}])
    _, second = intercom_devices.create(owner.id, user_agent=IPHONE)
    check("a second intercom credential from the same user agent is another device", second["device_id"] != core)
    crew_devices.add(owner.id, "hostname", "justins-iphone", "", "admin")
    eq("no user agent, no join: its own device", len(devices.listing(owner.id)), 3)

with section("a browser that subscribes again keeps its device"):
    notifications.subscribe(owner.id, subscription("a"), "another browser")
    eq("same endpoint, same device", db.one("select device_id from crew_push_subs where endpoint like '%/a'")["device_id"],
       core)

with section("forgetting a phone ends every part"):
    ours = devices.revoke(core)
    eq("it says what it ended", set(ours["parts"]), {"signin", "intercom", "crew_checkin", "crew_push"})
    eq("the intercom credential is gone", db.one("select count(*) n from intercom_devices where id = ?", (row["id"],))["n"], 0)
    eq("the push subscription is gone", db.one("select count(*) n from crew_push_subs")["n"], 0)
    eq("the check-in device is gone", db.one("select count(*) n from crew_devices where value = '3c:22:fb:5e:90:40'")["n"], 0)
    eq("the sign-in trust is gone", users.device_for(token), None)
    eq("the credential no longer works", intercom_devices.authenticate(cred), None)
    eq("a phone that is not there", devices.revoke(core), None)
    eq("another user's phone is not theirs to forget", devices.revoke(second["device_id"], user_id=owner.id + 99), None)

with section("a PIN session from a forgotten phone ends with it"):
    users.set_pin(owner, "correct horse", "4821")
    token, trusted = users.trust_device("Lobby", owner, "kiosk")
    dev = users.device_for(token)
    session, _ = users.start_session(owner, via="pin", device_id=dev["id"])
    check("the PIN session works", users.session_user(session) is not None)
    devices.revoke(db.one("select device_id from trusted_devices where id = ?", (dev["id"],))["device_id"])
    eq("it is signed out", users.session_user(session), None)

with section("a part of a module that is off goes too, and a merge keeps it"):
    devices.unregister("intercom")           # what switching the module off does
    _, a = intercom_devices.create(owner.id, user_agent="x1")
    _, b = intercom_devices.create(owner.id, user_agent="x2")
    crew_devices.add(owner.id, "mac", "aa:bb:cc:dd:ee:01", "", "self", "x2")
    devices.merge(a["device_id"], b["device_id"])
    eq("the merged phone holds all three parts", db.one("select count(*) n from intercom_devices where device_id = ?",
                                                       (a["device_id"],))["n"], 2)
    eq("including the check-in part", db.one("select count(*) n from crew_devices where device_id = ?",
                                            (a["device_id"],))["n"], 1)
    eq("the other device is gone", devices.get(b["device_id"]), None)
    devices.revoke(a["device_id"])
    eq("forgetting it removes the intercom part although the module is off",
       db.one("select count(*) n from intercom_devices where id in (?, ?)", (a["id"], b["id"]))["n"], 0)
    modules.attach("intercom")

with section("merge and rename refuse what they should"):
    _, m1 = intercom_devices.create(owner.id, user_agent="m1")
    for label, key, call in (
            ("itself", "merge_self", lambda: devices.merge(m1["device_id"], m1["device_id"])),
            ("a phone that is not there", "no_device", lambda: devices.merge(m1["device_id"], 99999)),
            ("an empty name", "name", lambda: devices.rename(m1["device_id"], "  ")),
            ("someone else's phone", "no_device", lambda: devices.rename(m1["device_id"], "x", user_id=owner.id + 99))):
        try:
            call()
            check(f"{label} is refused", False)
        except devices.Refused as exc:
            eq(f"{label} is refused", exc.key, key)
    devices.rename(m1["device_id"], "  Ann's   phone ")
    eq("a name is tidied", devices.get(m1["device_id"])["name"], "Ann's phone")

with section("the routes"):
    web = _db.client(app, base_url="http://relay.local:8001")
    web.post("/admin/login", data={"username": "owner", "password": "correct horse"}, follow_redirects=False)
    got = web.get("/api/admin/phones").json()["phones"]
    check("the admin sees every phone", any(p["id"] == m1["device_id"] for p in got))
    eq("each carries its ref", [p["ref"] for p in got if p["id"] == m1["device_id"]], [devices.ref(m1["device_id"])])
    eq("rename", web.put(f"/api/admin/phones/{m1['device_id']}", json={"name": "Booth"}).status_code, 200)
    eq("a phone of one's own", [p["name"] for p in web.get("/api/admin/me/phones").json()["phones"]
                                 if p["id"] == m1["device_id"]], ["Booth"])
    eq("merge needs a phone to join", web.post(f"/api/admin/phones/{m1['device_id']}/merge", json={}).status_code, 404)
    eq("forget", web.delete(f"/api/admin/phones/{m1['device_id']}").status_code, 200)
    eq("it is gone", devices.get(m1["device_id"]), None)
    eq("forgetting it again", web.delete(f"/api/admin/phones/{m1['device_id']}").status_code, 404)


with section("the migrations carry what was already enrolled"):
    root = Path(__file__).resolve().parent.parent / "app"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        create table users (id integer primary key);
        create table trusted_devices (id integer primary key autoincrement, name text not null, token_hash text not null,
            created_by integer references users (id) on delete set null, created_at text not null, last_used text);
        create table intercom_devices (id integer primary key autoincrement, user_id integer not null, name text not null default '',
            platform text not null default 'web', user_agent text not null default '', credential_hash text not null,
            issued_at text not null, last_seen_at text, revoked_at text);
        insert into users values (1), (2);
        insert into trusted_devices (name, token_hash, created_by, created_at) values ('Booth', 'a', 1, '2026-01-01T00:00:00Z'),
            ('Orphan', 'b', null, '2026-01-02T00:00:00Z'), ('Lobby', 'c', 2, '2026-01-03T00:00:00Z');
        insert into intercom_devices (user_id, name, credential_hash, issued_at, revoked_at) values
            (1, 'Ann', 'h1', '2026-02-01T00:00:00Z', null), (2, '', 'h2', '2026-02-02T00:00:00Z', null),
            (2, 'Old', 'h3', '2026-02-03T00:00:00Z', '2026-02-04T00:00:00Z');
    """)
    for path in (root / "migrations" / "0009_devices.sql", root / "modules" / "intercom" / "migrations" / "0005_core_device.sql"):
        for stmt in db._statements(path.read_text(encoding="utf-8")):
            conn.execute(stmt)
    got = [dict(r) for r in conn.execute("select name, device_id from trusted_devices order by id")]
    eq("trusted devices with an owner become devices", [g["name"] for g in got if g["device_id"]], ["Booth", "Lobby"])
    eq("one without an owner stays as it was", [g["device_id"] for g in got if g["name"] == "Orphan"], [None])
    phones = [dict(r) for r in conn.execute("select i.id, i.device_id, d.user_id, d.name from intercom_devices i"
                                            " left join devices d on d.id = i.device_id order by i.id")]
    eq("each signed-in credential gets its own device after the first migration's", [p["device_id"] for p in phones],
       [3, 4, None])
    eq("owned by the credential's user", [p["user_id"] for p in phones], [1, 2, None])
    eq("an unnamed one is called Phone", [p["name"] for p in phones], ["Ann", "Phone", None])
    conn.close()

done()
