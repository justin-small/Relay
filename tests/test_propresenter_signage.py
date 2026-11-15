"""ProPresenter and signage (#208): announcement slides copied into signage as pictures and a playlist, and the
slide-note tags that switch screens (parsing, the layer, pushes, live views, resume), with signage on and off.

ProPresenter is a fake Client answering from tables; nothing touches a network. Run: python tests/test_propresenter_signage.py
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

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402,F401
from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.propresenter import propresenter as pp, signagesync, signagetags, slidetags  # noqa: E402
from app.modules.propresenter.signagesync import PLAYLIST_NAME, PREFIX, Sync  # noqa: E402
from app.modules.propresenter.ppcontrol import Control  # noqa: E402
from app.modules.signage import schedule, store, takeover  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


def jpeg(tag):
    return b"\xff\xd8\xff\xe0" + tag.encode() + b"\x00" * 40


class FakePP(pp.Client):
    def __init__(self):
        super().__init__("127.0.0.1", 1, False)
        self.sent = []
        self.pics = {}          # (item index, cue) -> tag of the picture ProPresenter draws
        self.slides = {"P-A": ["a1", "a2"], "P-B": ["b1"]}
        self.items = [self.item(0, "Heading", "header", ""), self.item(1, "Welcome", "presentation", "P-A"),
                      self.item(2, "Video", "media", ""), self.item(3, "Coffee", "presentation", "P-B")]

    @staticmethod
    def item(i, name, kind, uuid):
        o = {"id": {"uuid": f"I{i}", "name": name, "index": i}, "type": kind, "is_hidden": False, "is_pco": False}
        if kind == "presentation":
            o["presentation_info"] = {"presentation_uuid": uuid}
        return o

    def _send(self, method, path, body=None, *, timeout=3, raw_body=False):
        self.sent.append(path)
        if path == "/v1/playlist/PL-ANN":
            return {"id": {"uuid": "PL-ANN", "name": "Announcements", "index": 0}, "items": self.items}
        if path.startswith("/v1/presentation/") and "/thumbnail" not in path:
            u = path.rsplit("/", 1)[1]
            return {"presentation": {"id": {"uuid": u, "name": u}, "groups": [
                {"name": "Slides", "slides": [{"text": t} for t in self.slides[u]]}]}}
        if path.startswith("/v1/playlist/PL-ANN/") and "/thumbnail/" in path:
            i, c = path.split("/")[4], path.split("/thumbnail/")[1].split("?")[0]
            quality = path.split("quality=")[1].split("&")[0]
            return ("image/jpeg", jpeg(f"{self.pics.get((int(i), int(c)), f'{i}-{c}')}-{quality}"))
        raise AssertionError(f"unexpected {method} {path}")

    def thumbs(self):
        return [p for p in self.sent if "/thumbnail/" in p]


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"propresenter": {"enabled": True}, "signage": {"enabled": True},
                         "planning_center": {"enabled": True}}})
_modules.attach("planning_center")
_modules.attach("propresenter")
_modules.attach("signage")


def save_pp(**kw):
    cur = config.get()["propresenter"]
    config.save({"propresenter": pp.clean_stored({**cur, **kw})}, replace=("propresenter",))


save_pp(enabled=True, host="127.0.0.1", port=1)
admin = _db.client(app, base_url="http://relay.local:8001")
fake = FakePP()
fol = pp.follower


async def _no_poke():
    pass


fol.poke = _no_poke
fol.client, fol.up = fake, True
sync = Sync(fol, Control(fol))
run = asyncio.run


print("settings")
eq("off and no playlist by default", pp.default_signage(), {"enabled": False, "playlist": ""})
eq("a bad playlist id is refused", pp.signage_fields({"playlist": "a b!"})[1], "That is not a ProPresenter playlist.")
eq("a key left out stays out", pp.signage_fields({"enabled": True})[0], {"enabled": True})
eq("the page saves them", admin.post("/api/admin/propresenter", json={"signage": {"enabled": True, "playlist": "PL-ANN"}}).status_code, 200)
eq("they are stored", config.get()["propresenter"]["signage"], {"enabled": True, "playlist": "PL-ANN"})
eq("a bad one is a 400", admin.post("/api/admin/propresenter", json={"signage": {"playlist": "x y"}}).status_code, 400)

print("announcement slides as signage items")
st = run(sync.sync())
eq("no error", st["error"], "")
eq("three slides (a header and a video are skipped)", st["slides"], 3)
lists = [p for p in store.playlists() if p["name"] == PLAYLIST_NAME]
eq("one signage playlist was made", len(lists), 1)
items = lists[0]["items"]
eq("three items in order", [i["name"] for i in items], [f"{PREFIX}Welcome 1", f"{PREFIX}Welcome 2", f"{PREFIX}Coffee 1"])
eq("they are plain library pictures", {i["source"] for i in items}, {"media"})
eq("each has a default time", {i["duration_s"] for i in items}, {10})
big = [p for p in fake.thumbs() if "quality=1280" in p]
eq("large pictures were fetched", len(big), 3)
n = len(fake.sent)
run(sync.sync())
eq("nothing is fetched again while the deck is unchanged beyond the checks", [p for p in fake.sent[n:] if "/thumbnail/" in p], [])
store.playlist_set_items(lists[0]["id"], [{**i, "duration_s": 25} if k == 0 else i for k, i in enumerate(items)])
fake.pics[(1, 1)] = "changed"
fake.slides["P-A"] = ["a1", "a2 changed"]
run(sync.sync())
items2 = next(p for p in store.playlists() if p["name"] == PLAYLIST_NAME)["items"]
eq("a changed slide gets a new picture, the rest are kept", [i["ref"] == j["ref"] for i, j in zip(items, items2)], [True, False, True])
eq("a kept slide keeps its time", items2[0]["duration_s"], 25)
check("the old picture was deleted", items[1]["ref"] not in {m["id"] for m in store.media_list()})
owned = [m for m in store.media_list() if m["name"].startswith(PREFIX)]
eq("only the playlist's pictures are left", len(owned), 3)
mine = store.media_add("Welcome poster", "0" * 64, "jpg", 5)
run(sync.sync(force=True))
check("an upload is never touched", store.media_get(mine["id"]) is not None)
fake.items = fake.items[:2]
run(sync.sync())
eq("a shortened playlist shrinks the signage one", len(next(p for p in store.playlists() if p["name"] == PLAYLIST_NAME)["items"]), 2)
fol.up = False
eq("ProPresenter away", run(sync.sync(force=True))["error"], "ProPresenter is not connected.")
fol.up = True
save_pp(signage={"enabled": True, "playlist": ""})
eq("no playlist picked", run(sync.sync())["error"], "Pick the announcement playlist first.")
save_pp(signage={"enabled": False, "playlist": "PL-ANN"})
eq("off does nothing", run(sync.sync())["error"], "")
save_pp(signage={"enabled": True, "playlist": "PL-ANN"})
config.save({"modules": {"signage": {"enabled": False}}})
eq("signage off", run(sync.sync(force=True))["error"], "Signage is switched off.")
config.save({"modules": {"signage": {"enabled": True}}})
eq("sync now answers", admin.post("/api/admin/propresenter/signage/sync").status_code, 200)

print("the tags are read")
for text, want in (("[relay:signage-screen=Lobby TV:Welcome]", [slidetags.Tag("signage-screen", "Lobby TV:Welcome")]),
                   ("[relay:signage-all=Welcome]", [slidetags.Tag("signage-all", "Welcome")]),
                   ("[relay:signage-captions]", [slidetags.Tag("signage-captions")]),
                   ("[relay:signage-captions-group=Lobby]", [slidetags.Tag("signage-captions-group", "Lobby")]),
                   ("[relay:SIGNAGE-RESUME]", [slidetags.Tag("signage-resume")]),
                   ("[relay:signage-push=Poster]", [slidetags.Tag("signage-push", "Poster")])):
    eq("parses " + text, slidetags.parse(text).slide, want)
eq("a value is needed", slidetags.parse("[relay:signage-screen]").slide, [])
eq("resume takes none for all screens", slidetags.parse("[relay:signage-resume=x]").slide, [])
eq("the existing tags are unchanged", slidetags.parse("[relay:lang=es][relay:lyrics]").slide,
   [slidetags.Tag("lang", "SPANISH"), slidetags.Tag("lyrics")])

print("the actions")
g_lobby = store.group_save(None, {"name": "Lobby"})
g_hall = store.group_save(None, {"name": "Hall: east"})
s1 = store.screen_save(None, {"name": "Lobby TV", "slug": "lobby-tv", "group_id": g_lobby["id"]})
s2 = store.screen_save(None, {"name": "Cafe", "slug": "cafe", "group_id": None})
s3 = store.screen_save(None, {"name": "Hall TV", "slug": "hall-tv", "group_id": g_hall["id"]})
welcome = store.playlist_create("Welcome")
kids = store.playlist_create("Kids: Sunday")
poster = store.media_add("Poster", "1" * 64, "png", 5)
video = store.media_add("Promo", "2" * 64, "mp4", 5)
A = signagetags.ACTIONS


def act(name, value=None):
    try:
        return run(A[name](value))
    except RuntimeError as exc:
        return "ERR " + str(exc)


def plays(screen):
    return store.resolve(store.screen_by_slug(screen))


eq("every action is wired", set(A), set(signagetags.NAMES))
check("one screen", act("signage-screen", "lobby tv:welcome").startswith("Lobby TV now plays Welcome"))
eq("its layer is tag", (plays("lobby-tv")["layer"], plays("lobby-tv")["playlist_id"]), ("tag", welcome["id"]))
eq("another screen is untouched", plays("cafe")["layer"], "none")
check("a group", act("signage-group", "Lobby:Kids: Sunday").startswith("Lobby now plays Kids: Sunday"))
eq("the screen's own beats its group's", plays("lobby-tv")["playlist_id"], welcome["id"])
check("a name with a colon", act("signage-group", "Hall: east:Welcome").startswith("Hall: east now plays Welcome"))
eq("the hall screen plays it", plays("hall-tv")["playlist_id"], welcome["id"])
check("all screens", act("signage-all", "Kids: Sunday").startswith("every screen now plays"))
eq("the cafe plays it", plays("cafe")["playlist_id"], kids["id"])
check("an unknown playlist says so", "no playlist called" in act("signage-all", "Nope"))
check("an unknown screen says so", "no screen called" in act("signage-screen", "Nobody:Welcome"))
check("a missing playlist half says how to write it", "Write it as" in act("signage-screen", "Lobby TV"))
store.override_set("screen", s2["id"], welcome["id"], "cleared")
eq("a tag beats a playlist put on by hand", plays("cafe")["layer"], "tag")
eq("the panel lists what tags hold", [h["playlist"] for h in signagetags.state()["held"]].count("Kids: Sunday") >= 1, True)

check("a push", act("signage-push", "poster").startswith("Sent Poster to every screen"))
check("it is a once push to all", takeover.rows()[0]["mode"] == "once" and takeover.rows()[0]["target"] == "all")
check("a push to a screen", act("signage-push-screen", "Cafe:Poster").startswith("Sent Poster to Cafe"))
check("a video is not a picture", "no picture called" in act("signage-push", "Promo"))
check("a slide by its short name", act("signage-push", "Welcome 1").startswith("Sent " + PREFIX + "Welcome 1"))
check("captions on one group", "shows the live captions" in act("signage-captions-group", "Lobby"))
check("service on all screens", "shows the live service" in act("signage-service"))
eq("an unknown view target", act("signage-captions-screen", "Nobody").startswith("ERR"), True)
check("resume one screen", act("signage-resume-screen", "Cafe").endswith("back on its playlist."))
eq("a resumed screen drops the tag", plays("cafe")["layer"] != "tag" or plays("cafe")["playlist_id"] == kids["id"], True)
check("resume all", act("signage-resume").startswith("Every screen is back"))
eq("nothing is taken over", takeover.rows(), [])
eq("no tag layer left", plays("lobby-tv")["layer"] != "tag", True)

print("signage off")
config.save({"modules": {"signage": {"enabled": False}}})
eq("a tag says signage is off", act("signage-all", "Welcome"), "ERR " + signagetags.OFF)
eq("the panel says so", signagetags.state(), {"on": False, "held": []})
config.save({"modules": {"signage": {"enabled": True}}})

print("through the watcher")
async def watch():
    ran = []
    t = slidetags.Tags()
    t.actions = {"signage-all": lambda v: _record(ran, v)}
    return t, ran


async def _record(ran, v):
    ran.append(v)
    return "ok"


async def drive():
    t, ran = await watch()
    t.observe(enabled=True, settle_s=0.05, uuid="U", title="Deck", index=0, notes="[relay:signage-all=Welcome]")
    await asyncio.sleep(0.3)
    return t, ran


t, ran = run(drive())
eq("a slide's tag reaches the action once settled", ran, ["Welcome"])
eq("the panel's resolved list shows it", t.seen["resolved"], ["signage-all=Welcome"])

print("the layer goes with the module")
from app.modules import modules as reg  # noqa: E402

reg.get("propresenter").detach()
eq("the layer is gone", schedule.layer_result(store.screen_by_slug("cafe"), datetime.now(timezone.utc)), None)
reg.get("propresenter").attach()
check("and comes back", "tag" in schedule._layers)

print()
print("all passed" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
