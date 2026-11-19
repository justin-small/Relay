"""Signage (#205): screens, groups, the media library, playlists, what a screen plays, the public player routes,
the signage port, and media mirrored into backups.

Offline, with a throwaway state folder and database. Run: python tests/test_signage.py
"""
import os
import socket
import sqlite3
import sys
import tempfile
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

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, serving  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.signage import live, media, schedule, sources, store  # noqa: E402
from app.modules.signage import takeover as tk  # noqa: E402
from app.alerts import alerts as relay_alerts  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 32

print("off")
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
admin = _db.client(app)
eq("admin API 404s while off", admin.get("/api/admin/signage").status_code, 404)
eq("player 404s while off", TestClient(app).get("/signage/lobby").status_code, 404)

config.save({"modules": {"signage": {"enabled": True}}})
_modules.attach("signage")
eq("default port", config.get()["signage"]["port"], 8090)

print("screens, groups and playlists")
r = admin.post("/api/admin/signage/playlists", json={"name": "  Sunday   lobby "})
eq("playlist made", r.status_code, 200)
pl = r.json()
eq("name tidied", pl["name"], "Sunday lobby")
eq("empty name refused", admin.post("/api/admin/signage/playlists", json={"name": " "}).status_code, 400)
pl2 = admin.post("/api/admin/signage/playlists", json={"name": "Youth"}).json()

eq("bad slug refused", admin.post("/api/admin/signage/screens", json={"name": "Lobby", "slug": "Lobby Left"}).status_code, 400)
eq("reserved-looking slug refused", admin.post("/api/admin/signage/screens", json={"name": "x", "slug": "_"}).status_code, 400)
scr = admin.post("/api/admin/signage/screens", json={"name": "Lobby left", "slug": "lobby-left",
                                                      "playlist_id": pl["id"], "sound": True}).json()
eq("screen made", scr["slug"], "lobby-left")
eq("sound kept", scr["sound"], True)
from app import modules as _registry  # noqa: E402
ndi_signage = _registry.using("ndi.outputs.signage")
check("signage offers its screens as NDI outputs while on", ndi_signage is not None)
eq("each screen is the player on this machine's signage port",
   [(d["id"], d["url"].split("/", 3)[-1], d["url"].startswith("http://127.0.0.1:")) for d in ndi_signage()],
   [("signage:lobby-left", "signage/lobby-left", True)])
eq("duplicate slug refused", admin.post("/api/admin/signage/screens", json={"name": "B", "slug": "lobby-left"}).status_code, 409)
eq("unknown playlist refused", admin.post("/api/admin/signage/screens", json={"name": "B", "slug": "b", "playlist_id": 999}).status_code, 404)

grp = admin.post("/api/admin/signage/groups", json={"name": "Lobby"}).json()
r = admin.put(f"/api/admin/signage/screens/{scr['id']}", json={**scr, "group_id": grp["id"]})
eq("screen joins group", r.json()["group_id"], grp["id"])

print("media library")
v0 = live.version
r = admin.post("/api/admin/signage/media?name=Welcome&width=1920&height=1080", content=PNG)
eq("image uploaded", r.status_code, 200)
img = r.json()
eq("kind sniffed", (img["kind"], img["ext"], img["mime"]), ("image", "png", "image/png"))
check("change bumps the version", live.version > v0)
stored = media.media_dir() / f"{img['sha256']}.png"
check("file stored under the state folder", stored.is_file() and str(stored).startswith(TMP))
check("file mirrored into backups", (media.mirror_dir() / stored.name).is_file())
eq("text refused", admin.post("/api/admin/signage/media?name=x", content=b"hello, this is not media at all").status_code, 415)
eq("empty refused", admin.post("/api/admin/signage/media?name=x", content=b"").status_code, 415)
old = media.IMAGE_LIMIT
media.IMAGE_LIMIT = 40
eq("image over the limit refused", admin.post("/api/admin/signage/media?name=big", content=PNG).status_code, 413)
media.IMAGE_LIMIT = old
check("refused uploads leave nothing behind", not [p for p in media.media_dir().iterdir() if p.name.startswith(".")])
vid = admin.post("/api/admin/signage/media?name=Promo&duration=12.5", content=MP4).json()
eq("video sniffed", (vid["kind"], vid["ext"]), ("video", "mp4"))
again = admin.post("/api/admin/signage/media?name=Welcome again", content=PNG).json()
eq("same file shares one stored copy", again["sha256"], img["sha256"])

eq("thumbnail refused when not an image", admin.put(f"/api/admin/signage/media/{img['id']}/thumb", content=b"nope" * 10).status_code, 415)
eq("thumbnail saved", admin.put(f"/api/admin/signage/media/{img['id']}/thumb", content=WEBP).status_code, 200)
eq("thumbnail served", admin.get(f"/api/admin/signage/media/{img['id']}/thumb").status_code, 200)

state = admin.get("/api/admin/signage").json()
eq("library lists three", len(state["media"]), 3)
check("storage readout", state["storage"]["used"] > 0 and "free" in state["storage"])
eq("rename", admin.patch(f"/api/admin/signage/media/{img['id']}", json={"name": "Welcome slide"}).status_code, 200)

print("items")
items = [
    {"source": "media", "ref": img["id"], "duration_s": 8, "transition": "cut", "fit": "fill"},
    {"source": "media", "ref": vid["id"], "duration_s": 0},
    {"source": "media", "ref": again["id"], "end_date": "2000-01-01"},
    {"source": "media", "ref": img["id"], "start_date": "2999-01-01"},
    {"source": "other_module", "ref": "x1"},
]
eq("bad date refused", admin.put(f"/api/admin/signage/playlists/{pl['id']}/items",
                                 json={"items": [{"source": "media", "ref": img["id"], "start_date": "2026-02-30"}]}).status_code, 400)
eq("end before start refused", admin.put(f"/api/admin/signage/playlists/{pl['id']}/items",
                                         json={"items": [{"source": "media", "ref": img["id"], "start_date": "2026-03-02",
                                                          "end_date": "2026-03-01"}]}).status_code, 400)
eq("unknown file refused", admin.put(f"/api/admin/signage/playlists/{pl['id']}/items",
                                     json={"items": [{"source": "media", "ref": "nope"}]}).status_code, 400)
eq("items saved", admin.put(f"/api/admin/signage/playlists/{pl['id']}/items", json={"items": items}).status_code, 200)
lists = {p["id"]: p for p in admin.get("/api/admin/signage").json()["playlists"]}
eq("order kept", [it["position"] for it in lists[pl["id"]]["items"]], [0, 1, 2, 3, 4])
check("an item from a module that is off is marked missing", lists[pl["id"]]["items"][4]["missing"])
eq("deleting media in use refused", admin.delete(f"/api/admin/signage/media/{img['id']}").status_code, 409)

print("what a screen plays")
s = store.screen_by_slug("lobby-left")
st = store.player_state(s, day="2026-11-14")
eq("screen's default when the group has none", st["playlist"], {"id": pl["id"], "name": "Sunday lobby", "from": "screen"})
eq("items outside their dates and from missing sources skipped", [i["id"] for i in st["items"]],
   [lists[pl["id"]]["items"][0]["id"], lists[pl["id"]]["items"][1]["id"]])
first = st["items"][0]
eq("item as the player wants it", (first["type"], first["url"], first["duration_s"], first["transition"], first["fit"]),
   ("image", f"/signage/_/media/{img['sha256']}.png", 8.0, "cut", "fill"))
eq("old dates still show on an earlier day", len(store.player_state(s, day="1999-12-31")["items"]), 3)

sources.register(sources.Source(kind="other_module", label="Other", resolve=lambda ref: {"type": "page", "url": "https://example.org/" + ref}))
eq("a registered source's items play", store.player_state(s, day="2026-11-14")["items"][-1]["url"], "https://example.org/x1")
sources.register(sources.Source(kind="other_module", label="Other", resolve=lambda ref: 1 / 0))
eq("a failing source skips its item", len(store.player_state(s, day="2026-11-14")["items"]), 2)
sources.register(sources.Source(kind="other_module", label="Other", resolve=lambda ref: {"type": "script", "url": "x"}))
eq("an unknown item type is skipped", len(store.player_state(s, day="2026-11-14")["items"]), 2)
sources.unregister("other_module")

admin.put(f"/api/admin/signage/groups/{grp['id']}", json={"name": "Lobby", "playlist_id": pl2["id"]})
eq("the group's playlist takes over", store.player_state(store.screen_by_slug("lobby-left"))["playlist"]["from"], "group")
admin.put(f"/api/admin/signage/groups/{grp['id']}", json={"name": "Lobby", "playlist_id": None})

dup = admin.post(f"/api/admin/signage/playlists/{pl['id']}/duplicate", json={}).json()
eq("duplicate named", dup["name"], "Sunday lobby (copy)")
lists = {p["id"]: p for p in admin.get("/api/admin/signage").json()["playlists"]}
eq("duplicate has the items", len(lists[dup["id"]]["items"]), 5)
eq("delete playlist", admin.delete(f"/api/admin/signage/playlists/{dup['id']}").status_code, 200)

print("public player routes")
tv = TestClient(app)
r = tv.get("/signage/lobby-left")
eq("player page", r.status_code, 200)
check("player page loads the script", "/signage/_/player.js?v=" in r.text and "lobby-left" in r.text)
eq("unknown screen", tv.get("/signage/nowhere").status_code, 404)
eq("script", tv.get("/signage/_/player.js").status_code, 200)
r = tv.get("/signage/_/state/lobby-left")
eq("state", (r.status_code, r.json()["screen"]["sound"]), (200, True))
check("state carries the server time", isinstance(r.json()["server_time"], int))
r = tv.get(first["url"])
eq("media served", (r.status_code, r.headers["content-type"], r.content), (200, "image/png", PNG))
check("media cached for good", "immutable" in r.headers["cache-control"])
eq("media range request", tv.get(f"/signage/_/media/{vid['sha256']}.mp4", headers={"Range": "bytes=0-3"}).status_code, 206)
eq("no path tricks", tv.get("/signage/_/media/..%2Fconfig.json").status_code, 404)
eq("now reported", tv.post("/signage/_/now/lobby-left", json={"item": first["id"], "label": "x"}).status_code, 200)
eq("now refused for an unknown screen", tv.post("/signage/_/now/zzz", json={"item": 1}).status_code, 404)
eq("now body limited", tv.post("/signage/_/now/lobby-left", content=b"{" + b" " * 2000 + b"}").status_code, 413)
sc = [x for x in admin.get("/api/admin/signage").json()["screens"] if x["slug"] == "lobby-left"][0]
eq("admin sees what the screen plays", sc["now"]["label"], "Welcome slide")
check("admin lists the screen address on the signage port", all(":8090/signage/lobby-left" in u for u in sc["urls"]))

r = admin.get("/api/admin/signage/preview?screen=lobby-left")
eq("preview maps media to the admin route", r.json()["items"][0]["url"], f"/api/admin/signage/file/{img['sha256']}.png")
eq("preview file", admin.get(r.json()["items"][0]["url"]).status_code, 200)
eq("playlist preview", admin.get(f"/api/admin/signage/preview?playlist={pl['id']}").status_code, 200)

print("permissions")
viewer = _db.client(app, "viewer")
eq("viewer reads", viewer.get("/api/admin/signage").status_code, 200)
eq("viewer cannot upload", viewer.post("/api/admin/signage/media?name=x", content=PNG).status_code, 403)
eq("viewer cannot add screens", viewer.post("/api/admin/signage/screens", json={"name": "a", "slug": "a"}).status_code, 403)
eq("signed out", TestClient(app, base_url="http://relay.local:8001").get("/api/admin/signage").status_code, 401)

print("settings")
eq("port below 1024 refused", admin.put("/api/admin/signage/settings", json={"port": 80}).status_code, 400)
eq("Relay's own port refused", admin.put("/api/admin/signage/settings", json={"port": 8001}).status_code, 400)
r = admin.put("/api/admin/signage/settings", json={"port": 8095})
eq("port saved", (r.status_code, config.get()["signage"]["port"]), (200, 8095))

print("the signage port")
serving.publish(8000, 8001)
serving.publish_listeners({8095: ("/signage",)})
on_tv_port = TestClient(app, base_url="http://relay.local:8095")
eq("player on the signage port", on_tv_port.get("/signage/lobby-left").status_code, 200)
eq("captions not on the signage port", on_tv_port.get("/").status_code, 404)
eq("admin not on the signage port", on_tv_port.get("/api/admin/signage").status_code, 404)
eq("static not on the signage port", on_tv_port.get("/static/viewer.css").status_code, 404)
eq("player not on the viewer port", TestClient(app, base_url="http://relay.local:8000").get("/signage/lobby-left").status_code, 404)
eq("player not on the panel port", admin.get("/signage/lobby-left").status_code, 404)
eq("admin still on the panel port", admin.get("/api/admin/signage").status_code, 200)
eq("health ok", _modules.health(_modules.get("signage")).state, "ok")
serving.publish_listeners({})
h = _modules.health(_modules.get("signage"))
eq("health asks for a restart when the port is not open", (h.state, "Restart" in h.detail), ("warn", True))
check("admin page says restart", admin.get("/api/admin/signage").json()["port"]["restart"])
serving.reset()

print("run.py opens module ports")
import run  # noqa: E402

probe = socket.socket()
probe.bind(("127.0.0.1", 0))
free = probe.getsockname()[1]
probe.close()
socks, lines = run._module_listeners("127.0.0.1", {**config.get(), "signage": {"port": free}}, {8000, 8001})
eq("one listener opened", len(socks), 1)
eq("published with its prefixes", serving.listener_prefixes(free), ("/signage",))
eq("unrouted, it binds the given host as before (#346)", list(socks[0].socks), ["127.0.0.1"])
for group in socks:
    for sk in group.socks.values():
        sk.close()
socks, _ = run._module_listeners("127.0.0.1", {**config.get(), "signage": {"port": 8001}}, {8000, 8001})
eq("a port Relay uses is skipped", len(socks), 0)
check("and said on the admin page", any("already uses" in n for n in serving.notices()))
busy = socket.socket()
busy.bind(("127.0.0.1", 0))
busy.listen(1)
taken = busy.getsockname()[1]
serving.reset()
socks, _ = run._module_listeners("127.0.0.1", {**config.get(), "signage": {"port": taken}}, set())
eq("a busy port does not stop Relay", len(socks), 0)
check("a busy port is a notice", any(f"{taken}" in n for n in serving.notices()))
busy.close()
serving.reset()
config.save({"modules": {"signage": {"enabled": False}}})
eq("no ports for a module that is off", run._module_listeners("127.0.0.1", config.get(), set())[0], [])
config.save({"modules": {"signage": {"enabled": True}}})

print("backups")
mirror = media.mirror_dir()
eq("mirror kept while the database names it", media.prune_mirror(store.media_hashes()), 0)
gone_sha = "f" * 64
(mirror / f"{gone_sha}.jpg").write_bytes(JPG)
snap = Path(os.environ["RELAY_DATABASE"]).parent / "snap"
snap.mkdir(exist_ok=True)
os.environ["RELAY_DB_BACKUPS"] = str(snap)
mirror_in_snap = media.mirror_dir()
mirror_in_snap.mkdir(parents=True, exist_ok=True)
(mirror_in_snap / f"{gone_sha}.jpg").write_bytes(JPG)
con = sqlite3.connect(snap / "relay-20261113-000000.db")
con.execute("create table signage_media (sha256 text)")
con.execute("insert into signage_media values (?)", (gone_sha,))
con.commit()
con.close()
eq("a copy a kept snapshot names stays", media.prune_mirror(store.media_hashes()), 0)
(snap / "relay-20261113-000000.db").unlink()
eq("a copy nothing names is pruned", media.prune_mirror(store.media_hashes()), 1)
del os.environ["RELAY_DB_BACKUPS"]
eq("unnamed copy in the default backups pruned", media.prune_mirror(store.media_hashes()), 1)

admin.put(f"/api/admin/signage/playlists/{pl['id']}/items", json={"items": []})
eq("delete media not in use", admin.delete(f"/api/admin/signage/media/{again['id']}").status_code, 200)
check("shared file kept while another upload uses it", stored.is_file())
eq("delete the last upload of a file", admin.delete(f"/api/admin/signage/media/{img['id']}").status_code, 200)
check("file deleted with it", not stored.exists())
check("backup copy stays until pruned", (media.mirror_dir() / stored.name).is_file())

print("schedules (#206)")
UTC = timezone.utc
pl3 = admin.post("/api/admin/signage/playlists", json={"name": "Events"}).json()
def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)
eq("signage keeps no zone of its own since #526", "timezone" in config.get()["signage"], False)
eq("signage reads the Relay zone", store.tz_name(), config.timezone())
eq("bad override setting refused", admin.put("/api/admin/signage/settings", json={"override_until": "never"}).status_code, 400)
config.save({"timezone": "America/Chicago"})
eq("override setting saved", admin.put("/api/admin/signage/settings", json={"override_until": "cleared"}).status_code, 200)
admin.put("/api/admin/signage/settings", json={"override_until": "next_change"})
scr_now = store.screen_by_slug("lobby-left")
sun = {"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"], "days": [0], "start_time": "09:00", "end_time": "11:30"}
for bad, why in (({"target": "tv"}, "bad target"), ({"playlist_id": 0}, "no playlist"), ({"days": []}, "no days or dates"),
                 ({"days": [7]}, "bad day"), ({"start_time": "9am"}, "bad time"), ({"end_time": "09:00"}, "same start and end"),
                 ({"start_time": "", "end_time": "09:00"}, "one time only"), ({"target_id": 999}, "unknown screen"),
                 ({"playlist_id": 999}, "unknown playlist"),
                 ({"start_date": "2026-12-01"}, "one date only"),
                 ({"start_date": "2026-12-02", "end_date": "2026-12-01"}, "range backwards")):
    eq("slot refused: " + why, admin.post("/api/admin/signage/slots", json={**sun, **bad}).status_code in (400, 404), True)
slot = admin.post("/api/admin/signage/slots", json=sun).json()
eq("slot summary", slot["id"] > 0 and store.slots()[0]["summary"], "Sun 9:00 AM-11:30 AM")
# 2026-11-15 is a Sunday; Chicago is UTC-6 then.
def plays(screen, when):
    return store.resolve(screen, when)
r = plays(scr_now, at(2026, 11, 15, 15, 0))
eq("Sunday 9:00 plays the slot", (r["playlist_id"], r["layer"]), (pl2["id"], "schedule"))
check("why names the slot", "Sun 9:00 AM-11:30 AM" in r["why"])
eq("end is exclusive", plays(scr_now, at(2026, 11, 15, 17, 30))["layer"], "screen")
eq("a weekday falls back to the default", plays(scr_now, at(2026, 11, 16, 15, 0))["playlist_id"], pl["id"])
eq("the player gets the slot's playlist", store.player_state(scr_now, now=at(2026, 11, 15, 15, 0))["playlist"]["from"], "schedule")

grp_slot = admin.post("/api/admin/signage/slots", json={"target": "all", "playlist_id": pl3["id"], "days": [0, 1],
                                                          "start_time": "08:00", "end_time": "12:00"}).json()
eq("a screen's own slot beats the all-screens one", plays(scr_now, at(2026, 11, 15, 15, 0))["playlist_id"], pl2["id"])
eq("the all-screens slot plays where the screen has none", plays(scr_now, at(2026, 11, 16, 15, 0))["playlist_id"], pl3["id"])
check("why counts other matches", "1 other slot" in plays(scr_now, at(2026, 11, 15, 15, 0))["why"])
g2 = admin.post("/api/admin/signage/groups", json={"name": "Hall"}).json()
admin.put(f"/api/admin/signage/screens/{scr_now['id']}", json={"name": "Lobby left", "slug": "lobby-left", "group_id": g2["id"],
                                                                "playlist_id": pl["id"]})
scr_now = store.screen_by_slug("lobby-left")
gs = admin.post("/api/admin/signage/slots", json={"target": "group", "target_id": g2["id"], "playlist_id": pl["id"], "days": [1],
                                                    "start_time": "08:00", "end_time": "12:00"}).json()
eq("a group's slot beats the all-screens one", plays(scr_now, at(2026, 11, 16, 15, 0))["playlist_id"], pl["id"])
eq("...and the layer says schedule", plays(scr_now, at(2026, 11, 16, 15, 0))["layer"], "schedule")
rng = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl3["id"],
                                                     "start_date": "2026-11-15", "end_date": "2026-11-15"}).json()
eq("a date range beats a weekly slot", plays(scr_now, at(2026, 11, 15, 15, 0))["playlist_id"], pl3["id"])
eq("a range with no times covers the whole day", plays(scr_now, at(2026, 11, 15, 7, 0))["playlist_id"], pl3["id"])
eq("the range ends with its last day", plays(scr_now, at(2026, 11, 16, 7, 0))["layer"] != "schedule" or plays(scr_now, at(2026, 11, 16, 7, 0))["playlist_id"] != pl3["id"], True)
for sl in (slot, grp_slot, gs, rng):
    admin.delete(f"/api/admin/signage/slots/{sl['id']}")
eq("slots gone", store.slots(), [])

late = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"],
                                                      "days": [6], "start_time": "22:00", "end_time": "02:00"}).json()
eq("a slot may cross midnight: before it", plays(scr_now, at(2026, 11, 15, 5, 0))["playlist_id"], pl2["id"])  # Sat 23:00 Chicago
admin.delete(f"/api/admin/signage/slots/{late['id']}")

print("layers, overrides and daylight saving")
a = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl["id"],
                                                   "days": [6], "start_time": "20:00", "end_time": "23:00"}).json()
b = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"],
                                                   "days": [6], "start_time": "21:00", "end_time": "02:00"}).json()
eq("overlap: the later start wins", plays(scr_now, at(2026, 11, 15, 4, 0))["playlist_id"], pl2["id"])  # Sat 22:00 Chicago
eq("before the later one starts, the earlier plays", plays(scr_now, at(2026, 11, 15, 2, 30))["playlist_id"], pl["id"])
eq("after midnight the crossing slot still plays", plays(scr_now, at(2026, 11, 15, 7, 30))["playlist_id"], pl2["id"])  # Sun 01:30
eq("and ends at 2:00", plays(scr_now, at(2026, 11, 15, 8, 0))["layer"], "screen")
for sl in (a, b):
    admin.delete(f"/api/admin/signage/slots/{sl['id']}")

eq("override needs a real playlist", admin.put("/api/admin/signage/override", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": 999}).status_code, 404)
eq("override cleared by hand lasts", admin.put("/api/admin/signage/override", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl3["id"], "until": "cleared"}).json()["expires_at"], "")
r = plays(scr_now, at(2030, 1, 1, 12))
eq("override beats the default", (r["playlist_id"], r["layer"]), (pl3["id"], "override"))
sched = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"],
                                                       "days": [0], "start_time": "09:00", "end_time": "11:30"}).json()
eq("override beats the schedule", plays(scr_now, at(2026, 11, 15, 15, 0))["layer"], "override")
eq("clearing it", admin.delete(f"/api/admin/signage/override?target=screen&target_id={scr_now['id']}").status_code, 200)
eq("clearing nothing is a 404", admin.delete(f"/api/admin/signage/override?target=screen&target_id={scr_now['id']}").status_code, 404)
eq("schedule plays again", plays(scr_now, at(2026, 11, 15, 15, 0))["layer"], "schedule")

# "until the next scheduled change": from Sunday 8:00 Chicago (14:00 UTC), the slot starts at 9:00.
rows = store.slots()
def key(local_now):
    return (schedule.matching(rows, scr_now, local_now) or [{"id": 0}])[0]["id"]
eq("next change is when the slot starts", schedule.next_change(key, at(2026, 11, 15, 14, 0), "America/Chicago"), at(2026, 11, 15, 15, 0))
eq("and the one after, when it ends", schedule.next_change(key, at(2026, 11, 15, 15, 0), "America/Chicago"), at(2026, 11, 15, 17, 30))
eq("no change inside the horizon is None", schedule.next_change(lambda _t: 1, at(2026, 11, 15, 15, 0), "America/Chicago"), None)
ov = {"target": "screen", "target_id": scr_now["id"], "playlist_id": pl3["id"], "set_at": "", "expires_at": "2026-11-15T15:00:00Z"}
eq("an override before it ends", store.resolve(scr_now, at(2026, 11, 15, 14, 59), _overrides=[ov])["layer"], "override")
eq("an override at its end", store.resolve(scr_now, at(2026, 11, 15, 15, 0), _overrides=[ov])["layer"], "schedule")
eq("override that ends at the next change is stored", admin.put("/api/admin/signage/override", json={"target": "all", "playlist_id": pl3["id"], "until": "next_change"}).status_code, 200)
check("it has an end", store.overrides()[0]["expires_at"].endswith("Z"))
admin.delete("/api/admin/signage/override?target=all&target_id=0")
admin.delete(f"/api/admin/signage/slots/{sched['id']}")

# Daylight saving, New York: clocks go forward at 02:00 EST (07:00 UTC) on 2026-03-08 and back on 2026-11-01.
config.save({"timezone": "America/New_York"})
dst = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"],
                                                     "days": [0], "start_time": "09:00", "end_time": "10:00"}).json()
eq("9:00 EST Sunday before the change", plays(scr_now, at(2026, 3, 1, 14, 0))["layer"], "schedule")
eq("9:00 EDT Sunday after the change", plays(scr_now, at(2026, 3, 8, 13, 0))["layer"], "schedule")
eq("so 14:00 UTC is 10:00 EDT, after it", plays(scr_now, at(2026, 3, 8, 14, 0))["layer"], "screen")
eq("9:00 EST again after the fall change", plays(scr_now, at(2026, 11, 1, 14, 0))["layer"], "schedule")
admin.delete(f"/api/admin/signage/slots/{dst['id']}")
skip = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"],
                                                      "days": [0], "start_time": "02:30", "end_time": "04:00"}).json()
eq("the skipped hour: nothing at 01:59", plays(scr_now, at(2026, 3, 8, 6, 59))["layer"], "screen")
eq("the slot starts at the first minute after it", plays(scr_now, at(2026, 3, 8, 7, 0))["layer"], "schedule")
admin.delete(f"/api/admin/signage/slots/{skip['id']}")
eq("a player's date follows the Relay zone", store.today(at(2026, 11, 15, 3, 0)), "2026-11-14")

# A slot may pick a named schedule from the Schedules page (#526) instead of keeping its own days and times.
from app import schedules as named_schedules
config.save({"timezone": "America/Chicago"})
svc = named_schedules.validate({"name": "Sunday services", "windows": [{"repeat": "weekly", "days": [0], "start_time": "09:00", "stop_time": "11:30"}]})
config.save({"schedules": [svc]})
ref = named_schedules.ref(svc["id"])
eq("a slot naming a schedule that does not exist is refused", admin.post("/api/admin/signage/slots", json={
    "target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"], "schedule_ref": "schedule:relay:nope"}).status_code, 404)
eq("a slot may not name something that is not a ref", admin.post("/api/admin/signage/slots", json={
    "target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"], "schedule_ref": "bogus"}).status_code, 400)
named = admin.post("/api/admin/signage/slots", json={"target": "screen", "target_id": scr_now["id"], "playlist_id": pl2["id"],
                                                       "schedule_ref": ref, "days": [3], "start_time": "01:00", "end_time": "02:00"}).json()
eq("its own days and times are not kept", (named["days"], named["start_time"], named["schedule_ref"]), ("", "", ref))
eq("the slot reads as the schedule", store.slots()[0]["summary"], "Schedule: Sunday services")
eq("it plays while the schedule is open", plays(scr_now, at(2026, 11, 15, 15, 0))["layer"], "schedule")
eq("and not outside it", plays(scr_now, at(2026, 11, 15, 18, 0))["layer"], "screen")
eq("the Schedules page is told where it is used", store.schedule_users(ref), [{"where": "Signage", "what": f"Slot on screen {scr_now['name']}"}])
config.save({"schedules": [dict(svc, windows=[dict(svc["windows"][0], start_time="10:00", stop_time="12:00")])]})
eq("moving the schedule moves the slot", (plays(scr_now, at(2026, 11, 15, 15, 30))["layer"], plays(scr_now, at(2026, 11, 15, 16, 30))["layer"]),
   ("screen", "schedule"))
config.save({"schedules": [dict(svc, enabled=False)]})
eq("a schedule that is off covers nothing", plays(scr_now, at(2026, 11, 15, 16, 30))["layer"], "screen")
config.save({"schedules": []})
eq("a deleted schedule covers nothing, and reads as deleted", (plays(scr_now, at(2026, 11, 15, 16, 30))["layer"],
                                                                store.slots()[0]["summary"]), ("screen", "Schedule: (deleted)"))
admin.delete(f"/api/admin/signage/slots/{named['id']}")

def takeover(screen, now):
    return {"playlist_id": pl3["id"], "why": "Alert"}
schedule.register_layer("takeover", takeover)
eq("a takeover beats the rest", plays(scr_now, at(2026, 11, 15, 15, 0))["layer"], "takeover")
schedule.unregister_layer("takeover")
try:
    schedule.register_layer("bogus", takeover)
    check("an unknown layer is refused", False)
except ValueError:
    check("an unknown layer is refused", True)
config.save({"timezone": "America/Chicago"})
admin.put(f"/api/admin/signage/screens/{scr_now['id']}", json={"name": "Lobby left", "slug": "lobby-left", "group_id": grp["id"], "playlist_id": pl["id"]})
admin.delete(f"/api/admin/signage/groups/{g2['id']}")
st_admin = admin.get("/api/admin/signage").json()
check("admin lists why each screen plays what it does", "why" in st_admin["screens"][0]["playing_playlist"])
eq("admin lists the zones", len(st_admin["timezones"]) > 0, True)

print("takeovers (#207)")
img = admin.post("/api/admin/signage/media?name=Push image&width=800&height=600", content=PNG + b"1").json()
vid = admin.post("/api/admin/signage/media?name=Push video&duration=5", content=MP4 + b"1").json()
def tstate():
    return tv.get("/signage/_/state/lobby-left").json()

def screen_row():
    return next(x for x in admin.get("/api/admin/signage").json()["screens"] if x["slug"] == "lobby-left")

eq("nothing taken over to start", (tstate()["takeover"], tstate()["banner"]), (None, None))
eq("a push needs a file", admin.put("/api/admin/signage/takeover", json={"kind": "push", "target": "screen", "target_id": scr["id"]}).status_code, 404)
eq("an unknown kind is refused", admin.put("/api/admin/signage/takeover", json={"kind": "x", "target": "all"}).status_code, 400)
eq("an unknown view is refused", admin.put("/api/admin/signage/takeover", json={"kind": "live", "target": "all", "view": "x"}).status_code, 400)
eq("a missing screen is refused", admin.put("/api/admin/signage/takeover", json={"kind": "live", "target": "screen", "target_id": 999, "view": "captions"}).status_code, 404)
eq("a bad time is refused", admin.put("/api/admin/signage/takeover", json={"kind": "push", "target": "all", "media_id": img["id"], "mode": "seconds", "seconds": 0}).status_code, 400)
eq("a viewer cannot take over", viewer.put("/api/admin/signage/takeover", json={"kind": "live", "target": "all", "view": "captions"}).status_code, 403)

r = admin.put("/api/admin/signage/takeover", json={"kind": "live", "target": "all", "view": "captions"})
eq("live view on all screens", r.status_code, 200)
t = tstate()["takeover"]
eq("player is told to frame captions", (t["kind"], t["view"], t["path"].startswith("/screen")), ("live", "captions", True))
check("player gets the viewer port", isinstance(t["port"], int))
eq("admin shows the screen taken over", screen_row()["taken_over"]["label"], "Live captions (translation)")

r = admin.put("/api/admin/signage/takeover", json={"kind": "push", "target": "group", "target_id": grp["id"], "media_id": img["id"], "mode": "once"})
eq("push to a group", r.status_code, 200)
t = tstate()["takeover"]
eq("a push beats a live view", (t["kind"], t["mode"], t["item"]["type"]), ("push", "once", "image"))
check("the pushed file is served by the player", t["item"]["url"].startswith("/signage/_/media/"))
eq("the screen reports it done", tv.post("/signage/_/done/lobby-left", json={"id": t["id"]}).status_code, 200)
eq("a finished once-push goes back to the live view", tstate()["takeover"]["kind"], "live")
eq("done for an unknown screen 404s", tv.post("/signage/_/done/nope", json={"id": 1}).status_code, 404)

admin.put("/api/admin/signage/takeover", json={"kind": "push", "target": "screen", "target_id": scr["id"], "media_id": vid["id"], "mode": "seconds", "seconds": 30})
t = tstate()["takeover"]
eq("a screen's own push beats its group's", (t["item"]["type"], t["mode"], bool(t["until"])), ("video", "seconds", True))
eq("clear one", admin.delete(f"/api/admin/signage/takeover?target=screen&target_id={scr['id']}").status_code, 200)
eq("clearing nothing 404s", admin.delete(f"/api/admin/signage/takeover?target=screen&target_id={scr['id']}").status_code, 404)

store.ready()
db.execute("update signage_takeovers set expires_at = '2000-01-01T00:00:00Z' where target = 'all'")
eq("an expired takeover is not shown (the group push is done here)", tstate()["takeover"], None)
tk.sweep()
eq("sweeping drops what ran out", [x["target"] for x in tk.rows()], ["group"])
eq("return all", admin.delete("/api/admin/signage/takeovers").json()["cleared"], 1)
eq("back to the playlist", tstate()["takeover"], None)
live.forget()
tk.live_view_set("all", 0, "service")
eq("another module can switch a view", tstate()["takeover"]["view"], "service")
tk.clear_all()

print("alert banner")
port_before = config.get()["signage"]["port"]
relay_alerts.save({"auto_stop_s": 120, "buttons": [{"name": "Nursery", "panels": [], "screens": ["foyer"]}]})
bid = relay_alerts.settings()["buttons"][0]["id"]
tk.reset_cache()
eq("no alert, no banner", tstate()["banner"], None)
relay_alerts.press(bid, "Sam")
tk.reset_cache()
b = tstate()["banner"]
eq("an alert shows as a banner", (b["text"], b["count"]), ("Nursery (from Sam)", 1))
eq("the playlist is untouched", tstate()["takeover"], None)
check("the state still carries the playlist", "playlist" in tstate())
eq("admin shows the banner", screen_row()["banner"]["count"], 1)
admin.put("/api/admin/signage/settings", json={"alert_screens": "groups", "alert_groups": [grp["id"]]})
tk.reset_cache()
eq("a chosen group still shows it", tstate()["banner"] is not None, True)
admin.put("/api/admin/signage/settings", json={"alert_screens": "groups", "alert_groups": []})
tk.reset_cache()
eq("an unchosen group does not", tstate()["banner"], None)
admin.put("/api/admin/signage/settings", json={"alert_screens": "none"})
tk.reset_cache()
eq("none shows nothing", tstate()["banner"], None)
eq("a bad alert mode is refused", admin.put("/api/admin/signage/settings", json={"alert_screens": "some"}).status_code, 400)
eq("settings keep the port", config.get()["signage"]["port"], port_before)
admin.put("/api/admin/signage/settings", json={"alert_screens": "all"})
tk.reset_cache()
relay_alerts.cancel(bid)
tk.reset_cache()
eq("cleared alert, no banner", tstate()["banner"], None)
eq("the stream signature follows the alerts", (tk.alert_sig(),), ((),))

print("delete screens and groups")
eq("delete group", admin.delete(f"/api/admin/signage/groups/{grp['id']}").status_code, 200)
eq("screen leaves the deleted group", store.screen_by_slug("lobby-left")["group_id"], None)
eq("delete screen", admin.delete(f"/api/admin/signage/screens/{scr['id']}").status_code, 200)
eq("player gone", tv.get("/signage/lobby-left").status_code, 404)

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
