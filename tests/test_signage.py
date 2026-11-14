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

from app import config, serving  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.signage import live, media, sources, store  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


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
eq("one socket opened", len(socks), 1)
eq("published with its prefixes", serving.listener_prefixes(free), ("/signage",))
for sk in socks:
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
