"""Planning Center: Next / Previous / Take control / Release from a panel (#80).

The fake LIVE answers the way the real one did when tried on a throwaway plan:
action paths with no LIVE id, 201 with the new Live document, Next and
Previous refused (403) without control, and toggle_control releasing control
when the token's owner already has it.

Offline. Run: python tests/test_pccontrol.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from fastapi.testclient import TestClient

import app.main as main
from app import config
from app.modules.planning_center import pclive, planningcenter as pc
from app.main import app
from app.modules.planning_center.servicefeed import feed as service_feed
from app.modules.planning_center.routes import control as panel_control
from app.panels import registry

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label + (f" ({extra})" if extra and not cond else ""), bool(cond), True)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"planning_center": {"enabled": True}}})  # the module is switched on (#157)
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")  # its hooks into core, without its background work
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = _db.client(app, base_url="http://relay.local:8001")

ME, OTHER = "9116316", "777"
ITEMS = ["1", "2", "3", "4"]


class Fake(pc.Client):
    """Planning Center's LIVE for one plan, as it behaved on a real account."""

    def __init__(self):
        super().__init__("id", "secret")
        self.controller = None     # person id
        self.cur = None            # index into ITEMS
        self.calls = []
        self.fail = None

    def _doc(self):
        inc, rel = [], {"controller": {"data": None}, "current_item_time": {"data": None},
                        "next_item_time": {"data": None}}
        if self.controller:
            rel["controller"]["data"] = {"type": "Person", "id": self.controller}
            inc.append({"type": "Person", "id": self.controller,
                        "attributes": {"full_name": "Relay Owner" if self.controller == ME else "Dana Ortiz"}})
        for name, idx in (("current_item_time", self.cur),
                          ("next_item_time", None if self.cur is None else self.cur + 1)):
            if idx is None and name == "next_item_time" and self.cur is None:
                idx = 0
            if idx is not None and idx < len(ITEMS):
                row = {"type": "ItemTime", "id": "t" + ITEMS[idx],
                       "attributes": {"live_start_at": "2026-11-10T19:00:00Z" if name.startswith("current") else None,
                                      "live_end_at": None, "exclude": False},
                       "relationships": {"item": {"data": {"type": "Item", "id": ITEMS[idx]}},
                                         "plan_time": {"data": {"type": "PlanTime", "id": "pt"}}}}
                rel[name]["data"] = {"type": "ItemTime", "id": row["id"]}
                inc.append(row)
        return {"data": {"type": "Live", "id": "700", "attributes": {}, "relationships": rel}, "included": inc}

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if self.fail:
            raise self.fail
        if path == "/services/v2/me":
            return {"data": {"type": "Person", "id": ME, "attributes": {"full_name": "Relay Owner"}}}
        assert path == "/services/v2/service_types/7/plans/700/live", path
        return self._doc()

    def post(self, path, params=None):
        self.calls.append(("POST", path))
        if self.fail:
            raise self.fail
        assert params == {"include": pc.LIVE_INCLUDE}
        base = "/services/v2/service_types/7/plans/700/live/"
        assert path.startswith(base), path
        action = path[len(base):]
        if action == "toggle_control":
            self.controller = None if self.controller == ME else ME
        elif self.controller != ME:
            raise pc.PlanningCenterError("auth", "no", status=403)
        elif action == "go_to_next_item":
            self.cur = 0 if self.cur is None else min(self.cur + 1, len(ITEMS) - 1)
        elif action == "go_to_previous_item":
            self.cur = max(0, (self.cur or 0) - 1)
        return self._doc()


fake = Fake()
f = pclive.follower


async def _no_poke():
    """Admin routes poke the follower; here its state is set by hand."""


f.poke = _no_poke
f._client = fake
config.save({"planning_center": {"app_id": "id", "secret": "secret", "service_types": ["7", "42"]}},
            replace=("planning_center",))
for st in ("7", "42"):
    f.tracks[st] = pclive.Track(st)
f.names.update({"7": "Sunday AM", "42": "Sunday PM"})
f.tracks["7"].plan = {"id": "700", "title": "", "series_title": "", "dates": "", "sort_date": 0,
                      "updated_at": "", "times": []}
f.tracks["7"].items = [{"id": i, "title": "Item " + i, "type": "item", "length": 300, "position": "during",
                        "sequence": n, "key": "", "arrangement": "", "arrangement_sequence": [], "notes": [],
                        "times": []} for n, i in enumerate(ITEMS)]

clock = [1000.0]
panel_control.clock = lambda: clock[0]


def pid(n):
    return f"{n:032x}"


A, B = pid(1), pid(2)
for p in (A, B):
    viewer.post("/api/panels/checkin", json={"id": p, "name": "Stage left" if p == A else "Booth"})


def press(p, action, key=None, type_="7", gap=1.1):
    clock[0] += gap
    body = {"type": type_}
    if key is not None:
        body["key"] = key
    return viewer.post(f"/api/panels/{p}/service/{action}", json=body)


print("off by default")
r = viewer.post("/api/panels/checkin", json={"id": A, "name": "Stage left"})
eq("check-in says off, no key", r.json()["service"], {"mode": "off", "service_type": None})
eq("a press is refused", (press(A, "next").status_code, press(A, "next").json()), (403, {"ok": False, "error": "not_allowed"}))
eq("…and nothing reaches Planning Center", fake.calls, [])

print("operator turns control on")
v = admin.post(f"/api/admin/panels/{A}", json={"service_mode": "show"}).json()
eq("show only", (v["service_mode"], registry.control_key(A)), ("show", None))
eq("show only cannot press", press(A, "next", key="x").json()["error"], "not_allowed")
v = admin.post(f"/api/admin/panels/{A}", json={"service_mode": "both"}).json()
key = registry.control_key(A)
check("both: a random key", key and len(key) == 32)
check("the key is not in the admin view", key not in json.dumps(v))
svc = viewer.post("/api/panels/checkin", json={"id": A, "name": "Stage left"}).json()["service"]
eq("check-in carries mode and key", svc, {"mode": "both", "service_type": None, "key": key})
eq("a bad mode is refused", admin.post(f"/api/admin/panels/{A}", json={"service_mode": "all"}).status_code, 400)
eq("a bad service type is refused", admin.post(f"/api/admin/panels/{A}", json={"service_type": "x"}).status_code, 400)
eq("a service type Relay does not follow is refused",
   admin.post(f"/api/admin/panels/{A}", json={"service_type": "8"}).status_code, 400)
eq("not on the viewer port",
   viewer.post(f"/api/admin/panels/{A}", json={"service_mode": "both"}).status_code in (403, 404), True)
admin.post(f"/api/admin/panels/{A}", json={"service_mode": "control"})
eq("switching between control modes keeps the key", registry.control_key(A), key)

print("refusals")
eq("no key", press(A, "next").json()["error"], "bad_key")
eq("wrong key", press(A, "next", key="0" * 32).status_code, 403)
eq("another panel's key", press(B, "next", key=key).json()["error"], "not_allowed")
eq("a type Relay does not follow", press(A, "next", key=key, type_="8").json()["error"], "not_followed")
eq("a followed type with no plan today", press(A, "next", key=key, type_="42").json()["error"], "no_plan")
eq("unknown action is 404", viewer.post(f"/api/panels/{A}/service/delete", json={"key": key}).status_code, 404)
eq("unknown panel is 404", viewer.post(f"/api/panels/{pid(9)}/service/next", json={"key": key}).status_code, 404)
eq("a large body is refused", viewer.post(f"/api/panels/{A}/service/next",
                                          content=b'{"key":"' + b"a" * 600 + b'"}').status_code, 413)
fake.calls.clear()
r = press(A, "next", key=key)
eq("Next before Take control", (r.status_code, r.json()["error"]), (409, "not_controlling"))

print("take control, next, previous, release")
r = press(A, "take_control", key=key)
eq("take control", (r.status_code, r.json()["ok"], r.json()["service"]["controller"]), (200, True, "Relay Owner"))
eq("everyone on the feed sees it at once", service_feed.payload("7")["controller"], "Relay Owner")
r = press(A, "take_control", key=key)
eq("again: nothing to do, still in control", (r.json()["service"]["controller"], fake.controller), ("Relay Owner", ME))
r = press(A, "next", key=key)
eq("next", r.json()["service"]["current_item"], "1")
r = press(A, "next", key=key)
eq("next again", (r.json()["service"]["current_item"], r.json()["service"]["next_item"]), ("2", "3"))
r = press(A, "previous", key=key)
eq("previous", r.json()["service"]["current_item"], "1")
eq("follower state moved", f.tracks["7"].live["current"]["item"], "1")
r = press(A, "release", key=key)
eq("release", (r.json()["service"]["controller"], fake.controller), (None, None))
r = press(A, "release", key=key)
eq("release again: nothing to do", (r.status_code, fake.controller), (200, None))
posts = [c[1].rsplit("/", 1)[1] for c in fake.calls if c[0] == "POST"]
eq("only the presses that change something reach Planning Center",
   posts, ["go_to_next_item", "toggle_control", "go_to_next_item", "go_to_next_item",
           "go_to_previous_item", "toggle_control"])

print("someone else has control")
fake.controller = OTHER
r = press(A, "take_control", key=key)
eq("take control toggles", (r.json()["service"]["controller"], fake.controller), ("Relay Owner", ME))
fake.controller = OTHER
r = press(A, "release", key=key)
eq("release leaves someone else's control alone", fake.controller, OTHER)

print("rate limit")
fake.controller = ME
press(A, "next", key=key)
r = press(A, "next", key=key, gap=0.1)
eq("a second press inside a quarter second", (r.status_code, r.json()["error"]), (429, "too_fast"))
eq("the next one a third of a second later works", press(A, "next", key=key, gap=0.3).status_code, 200)

print("Planning Center trouble")
fake.fail = pc.PlanningCenterError("network", "Could not reach Planning Center: timed out")
r = press(A, "next", key=key)
eq("unreachable", (r.status_code, r.json()), (502, {"ok": False, "error": "unreachable"}))
fake.fail = pc.PlanningCenterError("http", "Planning Center answered HTTP 422: secret stuff", status=422)
r = press(A, "previous", key=key)
eq("refused, without the reason", (r.json()["error"], "secret" in r.text), ("refused", False))
fake.fail = None

print("service type set in admin")
admin.post(f"/api/admin/panels/{A}", json={"service_type": "42"})
eq("the admin's type wins over the panel's", press(A, "next", key=key, type_="7").json()["error"], "no_plan")
eq("…and the panel is told", viewer.post("/api/panels/checkin", json={"id": A, "name": "Stage left"}).json()["service"]["service_type"], "42")
admin.post(f"/api/admin/panels/{A}", json={"service_type": None})

print("admin log")
acts = admin.get("/api/admin/planning-center/live").json()["actions"]
eq("newest first, with the panel's name",
   (acts[0]["panel_name"], acts[0]["action"], acts[0]["result"]), ("Stage left", "next", "no_plan"))
check("every press past the key is listed",
      {"ok", "unchanged", "not_controlling", "too_fast", "unreachable", "refused"} <= {a["result"] for a in acts})
check("refusals before the key are not (a stranger cannot flood it)",
      not any(a["result"] in ("bad_key", "not_allowed") for a in acts))

print("off again")
admin.post(f"/api/admin/panels/{A}", json={"service_mode": "off"})
eq("the key is dropped", (registry.control_key(A), press(A, "next", key=key).json()["error"]), (None, "not_allowed"))
eq("the stored entry keeps no key", "control_key" in json.dumps(config.get()["panels"]), False)
admin.post(f"/api/admin/panels/{A}", json={"service_mode": "control"})
check("turning it back on makes a new key", registry.control_key(A) not in (None, key))


async def stream_check():
    q = registry.subscribe(A)
    first = json.loads((await asyncio.wait_for(q.get(), 1)).decode().split("data: ", 1)[1])
    eq("an event stream opens with the mode and key", (first["type"], first["mode"], first["key"]),
       ("service_mode", "control", registry.control_key(A)))
    registry.update(A, service_mode="show")
    msg = json.loads((await asyncio.wait_for(q.get(), 1)).decode().split("data: ", 1)[1])
    eq("a change is pushed at once, without a key", msg, {"type": "service_mode", "mode": "show", "service_type": None})
    registry.unsubscribe(A, q)

asyncio.run(stream_check())

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
