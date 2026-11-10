"""Planning Center: the Service feed panels subscribe to (#79).

GET /stream?stream=service&type=<id> on the viewer port: the whole state on
connect, a new message only when something shown changes, a heartbeat between,
item notes only while the operator allows them, and `service` entries in
/api/targets. Caption feeds are unchanged.

Offline: the follower's tracked state is set by hand (test_pclive.py covers
how it is read from Planning Center).

Run: python tests/test_servicefeed.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from fastapi.testclient import TestClient

import app.main as main
from app import config, pclive
from app.main import app
from app.servicefeed import feed, show_notes

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label + (f" ({extra})" if extra and not cond else ""), bool(cond), True)


def unframe(frame: bytes) -> dict:
    line = next(l for l in frame.decode().splitlines() if l.startswith("data: "))
    return json.loads(line[6:])


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = TestClient(app, base_url="http://relay.local:8001", headers={"x-admin-token": "t0ken"})

NOW = 1_800_000_000.0
ITEMS = [
    {"id": "1", "title": "Countdown", "type": "media", "length": 300, "position": "pre", "sequence": 1,
     "key": "", "arrangement": "", "arrangement_sequence": [], "notes": [], "times": []},
    {"id": "2", "title": "Worship", "type": "header", "length": 0, "position": "during", "sequence": 2,
     "key": "", "arrangement": "", "arrangement_sequence": [], "notes": [], "times": []},
    {"id": "3", "title": "Great Song", "type": "song", "length": 240, "position": "during", "sequence": 3,
     "key": "G", "arrangement": "Radio", "arrangement_sequence": ["Verse 1", "Chorus 1"],
     "notes": [{"category": "Vocals", "content": "Pray for Sam's mother"}], "times": []},
    {"id": "4", "title": "Sermon", "type": "item", "length": 1500, "position": "during", "sequence": 4,
     "key": "", "arrangement": "", "arrangement_sequence": [], "notes": [], "times": []},
]
PLAN = {"id": "700", "title": "Sunday Morning", "series_title": "Autumn", "dates": "November 8, 2026",
        "sort_date": NOW, "updated_at": "", "times": [
            {"id": "7001", "type": "service", "name": "", "starts_at": NOW - 600, "ends_at": NOW + 5000,
             "live_starts_at": NOW - 600, "live_ends_at": None}]}


def item_time(item, started=None):
    return {"id": "t" + item, "item": item, "plan_time": "7001", "live_start_at": started,
            "live_end_at": None, "exclude": False}


f = pclive.follower


async def _no_poke():
    """Admin routes poke the follower; here its state is set by hand, and it must not call out."""


f.poke = _no_poke


def follow(ids):
    """Save a token and the followed ids, and set the follower's tracks by hand
    (its background task is not run here)."""
    saved = config.get()["planning_center"]
    config.save({"planning_center": {**saved, "app_id": "id", "secret": "secret", "service_types": ids}},
                replace=("planning_center",))
    for st in [s for s in f.tracks if s not in ids]:
        del f.tracks[st]
    for st in ids:
        f.tracks.setdefault(st, pclive.Track(st))
    f.names.update({"7": "Sunday AM", "42": "Sunday PM"})


def set_live(current, controller="Dana Ortiz", started=NOW - 60):
    tr = f.tracks["7"]
    tr.plan, tr.items, tr.updated_at = PLAN, [dict(i) for i in ITEMS], NOW
    if current is None:
        tr.live = {"id": None, "controller": None, "current": None, "next": None}
    else:
        nxt = str(int(current) + 1)
        tr.live = {"id": "99", "controller": {"id": "5", "name": controller} if controller else None,
                   "current": item_time(current, started),
                   "next": item_time(nxt) if any(i["id"] == nxt for i in ITEMS) else None}
    f._note_change()


print("off until connected")
eq("no service feeds in /api/targets", viewer.get("/api/targets").json()["service"], [])
eq("the feed is refused", viewer.get("/stream?stream=service&type=7").status_code, 404)

print("targets")
follow(["7", "42"])
f._note_change()
eq("one service feed per followed type",
   viewer.get("/api/targets").json()["service"],
   [{"service_type": "7", "label": "Sunday AM"}, {"service_type": "42", "label": "Sunday PM"}])
eq("a type Relay does not follow is refused", viewer.get("/stream?stream=service&type=8").status_code, 404)
eq("no type is refused", viewer.get("/stream?stream=service").status_code, 404)
eq("caption feeds still refuse a bad stream", viewer.get("/stream?stream=bogus").status_code, 400)
check("caption targets unchanged", {"source", "live", "running", "font_px"} <= set(viewer.get("/api/targets").json()))


class _Req:
    headers = {}
    query_params = {}


async def next_msg(resp, timeout=2.0):
    return unframe(await asyncio.wait_for(resp.body_iterator.__anext__(), timeout))


async def main_async():
    set_live("3")
    print("full state on connect")
    resp = await main.stream(_Req(), stream="service", type="7")
    m = await next_msg(resp)
    eq("type", (m["type"], m["service_type"], m["followed"], m["name"]), ("service", "7", True, "Sunday AM"))
    eq("plan", m["plan"], {"id": "700", "title": "Sunday Morning", "series_title": "Autumn",
                           "dates": "November 8, 2026"})
    eq("items in order with type and position",
       [(i["title"], i["type"], i["position"], i["length"]) for i in m["items"]],
       [("Countdown", "media", "pre", 300), ("Worship", "header", "during", 0),
        ("Great Song", "song", "during", 240), ("Sermon", "item", "during", 1500)])
    song = m["items"][2]
    eq("song key and arrangement sequence", (song["key"], song["arrangement"], song["sequence"]),
       ("G", "Radio", ["Verse 1", "Chorus 1"]))
    check("notes left out by default", "notes" not in song and m["notes"] is False and not show_notes())
    check("note text is nowhere in the message", "Sam" not in json.dumps(m))
    eq("current and next", (m["current_item"], m["next_item"]), ("3", "4"))
    eq("controller and live", (m["controller"], m["live"], m["reachable"]), ("Dana Ortiz", True, True))
    check("timers from the last message", m["item_started_at"] == NOW - 60 and m["item_length_s"] == 240
          and isinstance(m["item_left_s"], float) and m["server_time"] > 0, m)
    check("no secret", "secret" not in json.dumps(m))
    eq("one panel listening", feed.viewer_count(), 1)

    print("changes")
    f._note_change()
    feed.refresh()
    try:
        await asyncio.wait_for(resp.body_iterator.__anext__(), 0.3)
        check("nothing sent when nothing changed", False)
    except asyncio.TimeoutError:
        check("nothing sent when nothing changed", True)
        # The cancelled read closed the generator; open a fresh one.
        resp = await main.stream(_Req(), stream="service", type="7")
        await next_msg(resp)
    set_live("4", started=NOW)
    m = await next_msg(resp)
    eq("Next is sent at once", (m["current_item"], m["next_item"]), ("4", None))
    set_live(None, controller=None)
    m = await next_msg(resp)
    eq("nobody controlling is not live", (m["live"], m["controller"], m["current_item"]), (False, None, None))
    f.tracks["7"].reachable = False
    f.tracks["7"].error = "Could not reach Planning Center: timed out"
    f._note_change()
    m = await next_msg(resp)
    eq("unreachable, without the reason", (m["reachable"], "timed out" in json.dumps(m)), (False, False))
    f.tracks["7"].reachable, f.tracks["7"].error = True, None
    f._note_change()
    eq("and back", (await next_msg(resp))["reachable"], True)

    print("notes switch")
    r = admin.post("/api/admin/planning-center/notes", json={"show": True})
    eq("switch on", (r.status_code, r.json()["planning_center"]["show_notes"]), (200, True))
    m = await next_msg(resp)
    eq("notes reach panels at once", m["items"][2]["notes"], [{"category": "Vocals", "content": "Pray for Sam's mother"}])
    check("items without notes carry none", "notes" not in m["items"][3])
    admin.post("/api/admin/planning-center/notes", json={"show": False})
    m = await next_msg(resp)
    check("and leave when it is turned off", "Sam" not in json.dumps(m))
    eq("bad value refused", admin.post("/api/admin/planning-center/notes", json={"show": "yes"}).status_code, 400)
    check("not on the viewer port",
          viewer.post("/api/admin/planning-center/notes", json={"show": True}).status_code in (403, 404))
    admin.post("/api/admin/planning-center/notes", json={"show": True})
    await next_msg(resp)
    eq("survives a blank token save", (admin.post("/api/admin/planning-center", json={}).status_code,
                                       config.get()["planning_center"]["show_notes"]), (200, True))
    admin.post("/api/admin/planning-center/notes", json={"show": False})
    await next_msg(resp)

    print("heartbeat")
    real = main.HEARTBEAT_S
    main.HEARTBEAT_S = 0.05
    try:
        hb = await main.stream(_Req(), stream="service", type="7")
        await next_msg(hb)
        t = await next_msg(hb)
        eq("a tick between changes", (t["type"], t["service_type"], t["updated_at"]), ("service_tick", "7", NOW))
        await hb.body_iterator.aclose()
    finally:
        main.HEARTBEAT_S = real

    print("dropped from admin")
    follow(["42"])
    f._note_change()
    m = await next_msg(resp)
    eq("a panel still listening is told", (m["type"], m["followed"]), ("service", False))
    await resp.body_iterator.aclose()
    eq("disconnect unsubscribes", feed.viewer_count(), 0)


asyncio.run(main_async())

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
