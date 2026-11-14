"""Planning Center and signage (#209): the files attached to the followed plan and to its items, and text slides for
plan items, copied into signage's library and one playlist, with signage on and off.

Planning Center is a fake client answering from tables; nothing touches a network. Run: python tests/test_planning_center_signage.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

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
from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.planning_center import planningcenter as pc, signagesync  # noqa: E402
from app.modules.planning_center.pclive import Track, follower  # noqa: E402
from app.modules.planning_center.signagesync import KIND, PLAYLIST_NAME, PREFIX, Sync  # noqa: E402
from app.modules.signage import media, sources, store  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


def jpeg(tag):
    return b"\xff\xd8\xff\xe0" + tag.encode() + b"\x00" * 40


def png(tag):
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00" * 40


def mp4(tag):
    return b"\x00\x00\x00\x18ftypmp42" + tag.encode() + b"\x00" * 40


def att(aid, name, ctype, url, updated="2026-11-01T10:00:00Z", linked=False):
    return {"id": aid, "name": name, "filename": name, "content_type": ctype, "size": 100, "url": url,
            "linked": linked, "updated_at": updated}


class FakePC:
    def __init__(self):
        self.calls = []
        self.downloads = []
        self.body = {
            "https://files/a1": jpeg("a1"), "https://files/v1": mp4("v1"), "https://files/p1": png("p1"),
            "https://files/a1b": jpeg("a1b"),
        }
        self.item_files = {
            "i1": [att("a1", "welcome.jpg", "image/jpeg", "https://files/a1"),
                   att("d1", "sheet.pdf", "application/pdf", "https://files/d1"),
                   att("l1", "link", "image/jpeg", "", linked=True)],
            "i2": [att("v1", "loop.mp4", "video/mp4", "https://files/v1")],
            "i3": [],
        }
        self.plan_files = [att("p1", "banner.png", "image/png", "https://files/p1")]

    def item_attachments(self, st, plan, item):
        self.calls.append(("item", item))
        return list(self.item_files.get(item, []))

    def plan_attachments(self, st, plan):
        self.calls.append(("plan", plan))
        return list(self.plan_files)

    def stream_file(self, url, limit):
        self.downloads.append(url)
        data = self.body[url]
        yield data[:30]
        yield data[30:]


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"planning_center": {"enabled": True}, "signage": {"enabled": True}}})
_modules.attach("planning_center")
_modules.attach("signage")
fake = FakePC()
sync = Sync(follower)
sync.make_client = lambda: fake
admin = _db.client(app, base_url="http://relay.local:8001")
run = asyncio.run


def save(**kw):
    cur = config.get()["planning_center"]
    config.save({"planning_center": pc.clean_stored({**cur, "service_types": ["77"],
                                                      "signage": {**cur["signage"], **kw}})},
                replace=("planning_center",))


def items(*rows):
    return [{"id": i, "title": t, "type": "item", "notes": n} for i, t, n in rows]


tr = Track("77")
tr.plan = {"id": "9", "title": "Sunday", "dates": "Nov 15", "updated_at": "x", "times": []}
tr.items = items(("i1", "Announcements", [{"category": "Announcement", "content": "Coffee after\nBring a friend"},
                                          {"category": "Private", "content": "secret"}]),
                 ("i2", "Worship <b>", []), ("i3", "Prayer", []))
tr.items.insert(0, {"id": "h", "title": "Header", "type": "header", "notes": []})
follower.tracks["77"] = tr
follower.names["77"] = "Sunday"

print("settings")
eq("off by default, files on, no playlist", signagesync.default_signage(),
   {"enabled": False, "service_type": "", "plan_files": True, "item_files": True, "text_slides": False,
    "note_category": "", "item_match": "", "playlist": False})
eq("a bad service type is refused", signagesync.signage_fields({"service_type": "a b"})[1],
   "That is not a Planning Center service type.")
eq("a key left out stays out", signagesync.signage_fields({"enabled": True})[0], {"enabled": True})
eq("a long word is refused", bool(signagesync.signage_fields({"item_match": "x" * 81})[1]), True)
eq("stored settings are cleaned", pc.clean_stored({"signage": {"enabled": "yes", "playlist": 1}})["signage"],
   signagesync.default_signage())

print("nothing happens while it is off or not set up")
st = run(sync.sync())
eq("off is quiet", (st["enabled"], st["error"], fake.calls), (False, "", []))
save(enabled=True)
eq("no service type is said", run(sync.sync())["error"], "Pick the service type first.")
save(enabled=True, service_type="88")
eq("a service type not followed is said", run(sync.sync())["error"],
   "That service type is not one Relay follows. Follow it above first.")
save(enabled=True, service_type="77")
config.save({"modules": {"signage": {"enabled": False}}})
eq("signage off is said", run(sync.sync())["error"], "Signage is switched off.")
eq("and nothing was fetched", fake.calls, [])
config.save({"modules": {"signage": {"enabled": True}}})

print("files attached to the plan and its items")
st = run(sync.sync())
eq("no error", st["error"], "")
eq("three playable files (a pdf and a link are skipped)", (st["files"], st["texts"]), (3, 0))
lib = {m["name"]: m for m in store.media_list() if m["name"].startswith(PREFIX)}
eq("named after the item and file", sorted(lib), [f"{PREFIX}Announcements welcome.jpg", f"{PREFIX}Sunday banner.png",
                                                    f"{PREFIX}Worship <b> loop.mp4"])
eq("no playlist unless asked for", [p for p in store.playlists() if p["name"] == PLAYLIST_NAME], [])
eq("each file came down once", sorted(fake.downloads), ["https://files/a1", "https://files/p1", "https://files/v1"])
fake.downloads.clear()
run(sync.sync(force=True))
eq("a forced pass downloads nothing that has not changed", fake.downloads, [])
fake.item_files["i1"][0] = att("a1", "welcome.jpg", "image/jpeg", "https://files/a1b", updated="2026-11-09T10:00:00Z")
run(sync.sync())
eq("a changed file is fetched again", fake.downloads, ["https://files/a1b"])
eq("and the old picture is gone", sorted(m["name"] for m in store.media_list() if m["name"].startswith(PREFIX)),
   sorted(lib))
fake.item_files["i2"] = []
run(sync.sync())
eq("a file taken off the plan leaves the library",
   sorted(m["name"] for m in store.media_list() if m["name"].startswith(PREFIX)),
   [f"{PREFIX}Announcements welcome.jpg", f"{PREFIX}Sunday banner.png"])

print("a file nobody here made is never touched")


async def _chunks(b):
    yield b


sha, ext, size = run(media.receive(_chunks(jpeg("uploaded"))))
row = store.media_add("Lobby photo", sha, ext, size)
fake.plan_files = []
run(sync.sync())
check("the upload is still there", store.media_get(row["id"]) is not None)
eq("the banner left with the plan", [m["name"] for m in store.media_list() if m["name"] == f"{PREFIX}Sunday banner.png"], [])

print("only some plan items")
save(item_match="announce")
fake.calls.clear()
st = run(sync.sync())
eq("only the matching item was asked", fake.calls, [("item", "i1")])
eq("and the plan's own files were left out", st["files"], 1)

print("text slides")
save(item_match="", text_slides=True, note_category="announcement", playlist=True)
fake.item_files["i2"] = [att("v1", "loop.mp4", "video/mp4", "https://files/v1")]
st = run(sync.sync())
eq("a slide for each real item (not the header)", (st["files"], st["texts"]), (2, 3))
pl = [p for p in store.playlists() if p["name"] == PLAYLIST_NAME][0]
eq("the playlist holds the files, then the text slides", [i["source"] for i in pl["items"]],
   ["media", "media", KIND, KIND, KIND])
texts = [i for i in pl["items"] if i["source"] == KIND]
eq("a text slide shows for twelve seconds", {i["duration_s"] for i in texts}, {12})
eq("a file shows for ten", {i["duration_s"] for i in pl["items"] if i["source"] == "media"}, {10})
ann = [i for i in texts if i["name"] == PREFIX + "Announcements"][0]
page = sources.resolve(KIND, ann["ref"])
eq("a text slide is a page", page["type"], "page")
body = unquote(page["url"].split(",", 1)[1])
check("the title and the chosen note are on it", "Announcements" in body and "Coffee after" in body and "Bring a friend" in body)
check("another note category is not", "secret" not in body)
prayer = [i for i in texts if i["name"] == PREFIX + "Prayer"][0]
check("an item with no note is its title alone", "<p>" not in unquote(
    sources.resolve(KIND, prayer["ref"])["url"]))
worship = [i for i in texts if "Worship" in i["name"]][0]
wbody = unquote(sources.resolve(KIND, worship["ref"])["url"].split(",", 1)[1])
check("what was typed in Planning Center is escaped", "&lt;b&gt;" in wbody and "Worship <b>" not in wbody)
eq("the source lists its slides", len(sources.get(KIND).choices()), 3)
eq("a missing slide resolves to nothing", sources.resolve(KIND, "nope"), None)

print("the playlist keeps what the operator set")
edited = [dict(i, duration_s=30) if i["source"] == KIND and i["name"].endswith("Prayer") else i for i in pl["items"]]
store.playlist_set_items(pl["id"], [{k: i[k] for k in ("source", "ref", "duration_s", "start_date", "end_date",
                                                        "transition", "fit")} for i in edited])
fake.item_files["i3"] = [att("a3", "extra.jpg", "image/jpeg", "https://files/a3")]
fake.body["https://files/a3"] = jpeg("a3")
run(sync.sync())
pl = [p for p in store.playlists() if p["name"] == PLAYLIST_NAME][0]
eq("an item that stayed kept its time", [i["duration_s"] for i in pl["items"] if i["name"].endswith("Prayer")], [30])
eq("and a new file joined", len(pl["items"]), 6)

print("the same file on two items is one file")
fake.item_files["i1"].append(att("a1", "welcome.jpg", "image/jpeg", "https://files/a1b", updated="2026-11-09T10:00:00Z"))
fake.item_files["i1"][0] = att("a1", "welcome.jpg", "image/jpeg", "https://files/a1b", updated="2026-11-09T10:00:00Z")
run(sync.sync(force=True))
eq("still six", len([i for p in store.playlists() if p["name"] == PLAYLIST_NAME for i in p["items"]]), 6)

print("no plan")
saved_plan, saved_plans = tr.plan, tr.plans
tr.plan, tr.plans = None, []
eq("no plan is said", run(sync.sync(force=True))["error"], "That service type has no current or upcoming plan.")
check("what was stored keeps playing", store.playlists())
tr.plan = None
tr.plans = [saved_plan]
eq("the next plan is used when none is on today", run(sync.sync(force=True))["error"], "")
tr.plan = saved_plan

print("a token that cannot be used")
sync.make_client = lambda: None
eq("not connected is said", run(sync.sync(force=True))["error"], "Planning Center is not connected.")


def boom(*a, **k):
    raise pc.PlanningCenterError("network", "no")


sync.make_client = lambda: fake
fake.plan_attachments = boom
eq("a failing Planning Center is said, and retried later", run(sync.sync(force=True))["error"],
   "Planning Center did not give Relay the plan's files; it will try again.")
check("what was stored is kept", [p for p in store.playlists() if p["name"] == PLAYLIST_NAME])

print("downloads only go where they should")
client = pc.Client("id", "secret")
for bad in ("http://files.example.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://[::1]/x", "ftp://x/y"):
    try:
        next(client.stream_file(bad, 10))
        got = "fetched"
    except pc.PlanningCenterError:
        got = "refused"
    eq(f"{bad} is refused", got, "refused")

print("the admin page")
eq("the source is registered", sources.get(KIND) is not None, True)
res = admin.get("/api/admin/planning-center/signage")
eq("the state is readable", res.status_code, 200)
eq("with the followed service types", res.json()["service_types"], [{"id": "77", "name": "Sunday"}])
res = admin.post("/api/admin/planning-center/signage/settings", json={"service_type": "x y"})
eq("a bad one is a 400", res.status_code, 400)
res = admin.post("/api/admin/planning-center/signage/settings", json={"enabled": False, "item_match": "Hi"})
eq("settings save", (res.status_code, config.get()["planning_center"]["signage"]["item_match"]), (200, "Hi"))
eq("the token is untouched", config.get()["planning_center"]["service_types"], ["77"])
eq("sync now while off is a 409", admin.post("/api/admin/planning-center/signage/sync", json={}).status_code, 409)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
