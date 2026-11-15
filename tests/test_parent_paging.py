"""Parent paging (#220): the Check-Ins reads, the settings and room links, the room page's rules (the link is the key,
HTTPS port only, never through the tunnel), the three ways a page reaches ProPresenter, and how pages end. Nothing
touches a network: Planning Center and ProPresenter are fakes. Run: python tests/test_parent_paging.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import config, serving  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.parent_paging import MODULE, checkins, paging, routes  # noqa: E402
from app.modules.parent_paging.paging import pager  # noqa: E402
from app.modules.planning_center import planningcenter as pc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------------------------------- fakes
class FakePC:
    """Answers the Check-Ins paths the module reads, and records what it was asked."""

    def __init__(self):
        self.asked = []
        self.kids = {"301": [("1", "Emma", "Stone", "x7k", None), ("2", "Liam", "", "B2M", None),
                             ("3", "Gone", "Home", "Q9Q", "2026-11-15T10:00:00Z"), ("4", "No", "Code", "", None)]}
        self.fail = None

    def get(self, path, params=None):
        self.asked.append((path, dict(params or {})))
        if self.fail:
            raise self.fail
        if path == "/check-ins/v2/events":
            return {"data": [{"id": "9", "attributes": {"name": "Sunday Kids"}}]}
        if path == "/check-ins/v2/events/9/locations":
            return {"data": [{"id": "300", "attributes": {"name": "Nursery wing", "kind": "Folder"}},
                             {"id": "301", "attributes": {"name": "Room 3", "kind": "Location"}},
                             {"id": "302", "attributes": {"name": "Babies", "kind": "Location"}}]}
        if path == "/check-ins/v2/check_ins":
            rows = self.kids.get(params["where[location_ids]"], [])
            return {"data": [{"id": i, "attributes": {"first_name": f, "last_name": l, "security_code": c,
                                                      "checked_out_at": out}} for i, f, l, c, out in rows]}
        raise AssertionError(path)


class FakePP:
    def __init__(self):
        self.sent = []
        self.message = {"id": {"uuid": "U", "name": "Parent Page", "index": 0}, "message": "Parent of {code}",
                        "tokens": [{"name": "code", "text": {"text": ""}}], "theme": {}, "visible_on_network": True}
        self.fail = None

    def _do(self, what):
        if self.fail:
            raise self.fail
        self.sent.append(what)

    def read(self, path):
        self.sent.append(("read", path))
        return self.message

    def put(self, path, body):
        self._do(("put", path, body["tokens"][0]["text"]["text"]))

    def post(self, path, body=None):
        self._do(("post", path, body))

    def action(self, path):
        self._do(("get", path))


async def direct(fn, *args):
    return fn(*args)


fake_pc, fake_pp = FakePC(), FakePP()

print("Check-Ins")
eq("codes are letters and digits, upper case", checkins.clean_code(" x7-k "), "X7K")
eq("a name is first name and initial", checkins.short_name("Emma ", "stone"), "Emma S.")
eq("no last name: the first name alone", checkins.short_name("Liam", ""), "Liam")
eq("rooms leave folders out", checkins.locations(fake_pc),
   [{"id": "302", "name": "Babies", "event": "Sunday Kids"}, {"id": "301", "name": "Room 3", "event": "Sunday Kids"}])
eq("events asked for are not archived", fake_pc.asked[0][1]["filter"], "not_archived")
fake_pc.asked.clear()
now = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
eq("a room's kids: checked in, with a code, not checked out", checkins.kids_in(fake_pc, "301", now),
   [{"id": "1", "name": "Emma S.", "code": "X7K"}, {"id": "2", "name": "Liam", "code": "B2M"}])
p = fake_pc.asked[0][1]
eq("asks for attendees not checked out, in this room, today", (p["filter"], p["where[location_ids]"],
                                                                p["where[created_at][gte]"]),
   ("attendee,not_checked_out", "301", "2026-11-15T03:00:00Z"))
eq("asks only for the fields it shows (no medical notes or contacts)", p["fields[CheckIn]"],
   "first_name,last_name,security_code,created_at,checked_out_at")
fake_pc.fail = pc.PlanningCenterError("auth", "secret text", status=403)
try:
    checkins.locations(fake_pc)
    eq("a refusal raises", True, False)
except checkins.Unavailable as exc:
    eq("a 403 says Check-Ins, in a fixed sentence", "Check-Ins" in exc.message and "secret" not in exc.message, True)
fake_pc.fail = None
eq("the Check-Ins version header goes only to Check-Ins",
   (pc.Client("a", "b")._headers("/check-ins/v2/events")["X-PCO-API-Version"],
    pc.Client("a", "b")._headers("/services/v2")["X-PCO-API-Version"]), (pc.CHECK_INS_VERSION, pc.API_VERSION))

print("settings")
eq("defaults", paging.clean_stored(None), {"mode": "queue", "message": "Parent Page", "token": "code",
                                           "timeout_min": 10, "rooms": []})
eq("a hand edit is cleaned", paging.clean_stored({"mode": "loud", "timeout_min": 999, "message": "  ",
                                                  "rooms": [{"id": "zz"}, "x"]}),
   {"mode": "queue", "message": "Parent Page", "token": "code", "timeout_min": 120, "rooms": []})

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "planning_center": {"app_id": "a", "secret": "b"},
             "modules": {"parent_paging": {"enabled": True}, "planning_center": {"enabled": True},
                         "propresenter": {"enabled": True}}})
MODULE.attach()
routes._pc_client = lambda: fake_pc
pager.client_for, pager.call = (lambda: fake_pp), direct
admin = _db.client(app, base_url="http://relay.local:8001")
phone = TestClient(app, base_url="https://relay.local:8001")
viewer = TestClient(app, base_url="http://relay.local:8000")
A = "/api/admin/parent-paging"

r = admin.get(A + "/locations")
eq("rooms load from Check-Ins", [l["id"] for l in r.json()["locations"]], ["302", "301"])
r = admin.post(A + "/rooms", json={"location_id": "301", "name": "Room 3", "event": "Sunday Kids"})
room = r.json()["room"]
eq("a room is added, and its secret is not in the admin state", ("secret" in room,
                                                                  "secret" in str(admin.get(A).json())), (False, False))
eq("a room needs a Check-Ins room", admin.post(A + "/rooms", json={"location_id": "x"}).status_code, 400)
link = admin.get(A + f"/rooms/{room['id']}/link").json()
eq("the link is on the address the admin uses", link["url"].startswith("http://relay.local:8001/paging/"), True)
path = link["path"]

print("the room page")
eq("the page opens with the link", phone.get(path).status_code, 200)
eq("a made-up link is not found", phone.get("/paging/" + "a" * 24).status_code, 404)
eq("never through the remote access tunnel", phone.get(path, headers={"cf-ray": "1"}).status_code, 404)
real_published = serving.published
serving.published = lambda: True
eq("never on the viewer port", viewer.get(path).status_code, 404)
serving.published = real_published
st = phone.get(path + "/state").json()
eq("the room's kids, nobody paged", [(k["name"], k["code"], k["page"]) for k in st["kids"]],
   [("Emma S.", "X7K", ""), ("Liam", "B2M", "")])
eq("a child not on the list cannot be paged", phone.post(path + "/page", json={"kid": "99"}).status_code, 404)

print("queue: waits for the operator")
fake_pp.sent.clear()
r = phone.post(path + "/page", json={"kid": "1"})
page_id = r.json()["page"]["id"]
eq("paged: waiting for the booth", r.json()["page"]["status"], "waiting")
eq("paging the same child twice is one page", phone.post(path + "/page", json={"kid": "1"}).json()["page"]["id"],
   page_id)
run(pager.sync())
eq("nothing goes to ProPresenter yet", fake_pp.sent, [])
eq("the room sees it marked", [k["page"] != "" for k in phone.get(path + "/state").json()["kids"]], [True, False])
st = admin.get(A).json()
eq("the operator sees the code and room, no name", (st["pages"][0]["code"], st["pages"][0]["room"],
                                                    "Emma" in str(st)), ("X7K", "Room 3", False))
admin.post(A + f"/pages/{page_id}/show")
run(pager.sync())
eq("Show triggers the message with the code", fake_pp.sent,
   [("post", "/v1/message/Parent%20Page/trigger", [{"name": "code", "text": {"text": "X7K"}}])])
phone.post(path + "/page", json={"kid": "2"})
st = admin.get(A).json()
admin.post(A + f"/pages/{st['pages'][1]['id']}/show")
run(pager.sync())
eq("two shown: both codes in the token", fake_pp.sent[-1][2], [{"name": "code", "text": {"text": "X7K   B2M"}}])
eq("the room cannot end another room's page", phone.post(path + "/arrived/zzzzzzzz").status_code, 404)
phone.post(path + f"/arrived/{page_id}")
run(pager.sync())
eq("parent arrived: the other code stays", fake_pp.sent[-1][2], [{"name": "code", "text": {"text": "B2M"}}])
admin.post(A + "/pages/clear")
run(pager.sync())
eq("cleared: the message is hidden", fake_pp.sent[-1], ("get", "/v1/message/Parent%20Page/clear"))

print("auto: on screen at once")
admin.put(A + "/settings", json={"mode": "auto"})
fake_pp.sent.clear()
r = phone.post(path + "/page", json={"kid": "2"})
eq("paged: on screen", r.json()["page"]["status"], "shown")
run(pager.sync())
eq("triggered straight away", fake_pp.sent[-1][0], "post")

print("fill: the token is filled, the operator shows it")
admin.put(A + "/settings", json={"mode": "fill"})
run(pager.sync())
eq("switching way writes the token again", fake_pp.sent[-1], ("put", "/v1/message/Parent%20Page", "B2M"))
eq("the room sees it sent", phone.get(path + "/state").json()["pages"][0]["status"], "sent")
admin.post(A + "/pages/clear")
run(pager.sync())
eq("cleared: the token is emptied and the message Relay showed is hidden",
   fake_pp.sent[-2:], [("put", "/v1/message/Parent%20Page", ""), ("get", "/v1/message/Parent%20Page/clear")])
fake_pp.message = {**fake_pp.message, "tokens": [{"name": "other", "text": {"text": ""}}]}
phone.post(path + "/page", json={"kid": "1"})
run(pager.sync())
eq("a message without the token is a fixed sentence", pager.error,
   "The ProPresenter message has no text token with that name.")
eq("the module's health says so", MODULE.health().state, "warn")
fake_pp.message["tokens"] = [{"name": "code", "text": {"text": ""}}]
run(pager.sync())
eq("fixed: the next round writes it", (pager.error, fake_pp.sent[-1]), ("", ("put", "/v1/message/Parent%20Page", "X7K")))
admin.post(A + "/pages/clear")
run(pager.sync())

print("view-only and timeouts")
admin.put(A + "/settings", json={"mode": "auto", "timeout_min": 5})


class ViewOnlyErr(Exception):
    kind = "view_only"


fake_pp.fail = ViewOnlyErr("raw")
phone.post(path + "/page", json={"kid": "1"})
run(pager.sync())
eq("view-only mode is named", "view-only" in pager.error, True)
fake_pp.fail = None
eq("a page younger than the timeout stays", pager.expire(), 0)
eq("one older is taken down", pager.expire(now=pager.pages[0].at + 301), 1)
run(pager.sync())
eq("and the message is hidden", fake_pp.sent[-1], ("get", "/v1/message/Parent%20Page/clear"))

print("links and rooms")
new = admin.post(A + f"/rooms/{room['id']}/new-link").json()
eq("a new link works", phone.get(new["path"]).status_code, 200)
eq("the old one stops at once", phone.get(path).status_code, 404)
phone.post(new["path"] + "/page", json={"kid": "2"})
admin.delete(A + f"/rooms/{room['id']}")
eq("removing a room ends its pages and its link", (len(pager.pages), phone.get(new["path"]).status_code), (0, 404))

print("permissions and the module switch")
eq("Check-Ins rooms need the manage permission", phone.get(A + "/locations").status_code, 401)
config.save({"modules": {"parent_paging": {"enabled": False}}})
eq("off: the room pages are gone", phone.get(new["path"]).status_code, 404)
eq("off: the admin routes are gone", admin.get(A).status_code, 404)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
