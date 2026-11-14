"""Signage web pages and YouTube (#210): link parsing, what the player is told, the embedding checks and the admin
routes, with signage on and off. Nothing touches a network: the fetch is replaced. Run: python tests/test_signage_web.py
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
from email.message import Message
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
from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.signage import sources, store  # noqa: E402
from app.modules.signage_web import links  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


def hdrs(**kw):
    m = Message()
    for k, v in kw.items():
        m[k.replace("_", "-")] = v
    return m


print("YouTube addresses")
V = "dQw4w9WgXcQ"
for url in (f"https://www.youtube.com/watch?v={V}", f"https://youtu.be/{V}?t=4", f"https://www.youtube.com/embed/{V}",
            f"https://www.youtube.com/shorts/{V}", f"https://m.youtube.com/watch?v={V}&feature=share"):
    eq(url, links.parse_youtube(url), {"video": V, "list": ""})
L = "PLabcdefghijk123"
eq("a playlist", links.parse_youtube(f"https://www.youtube.com/playlist?list={L}"), {"video": "", "list": L})
eq("a video inside a playlist", links.parse_youtube(f"https://www.youtube.com/watch?v={V}&list={L}"),
   {"video": V, "list": L})
eq("a YouTube mix is not a playlist", links.parse_youtube(f"https://www.youtube.com/watch?v={V}&list=RDdQw4w9WgXcQ"),
   {"video": V, "list": ""})
eq("the home page names nothing", links.parse_youtube("https://www.youtube.com/"), None)
eq("another site is not YouTube", links.parse_youtube(f"https://evil.example/watch?v={V}"), None)
eq("a lookalike host is not YouTube", links.parse_youtube(f"https://youtube.com.evil.example/watch?v={V}"), None)

print("links")
eq("a bare address gets https", links.clean_link({"url": "example.org/menu"})["url"], "https://example.org/menu")
for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x.org/a", "", "https://u:p@example.org/",
            "https://example.org/a b", "https://www.youtube.com/"):
    try:
        links.clean_link({"url": bad})
        eq("refuses " + repr(bad), True, False)
    except links.Invalid:
        eq("refuses " + repr(bad), True, True)
a = links.clean_link({"url": f"https://youtu.be/{V}", "name": "  Welcome   loop "})
eq("a YouTube address is a youtube link", (a["kind"], a["name"]), ("youtube", "Welcome loop"))
eq("stored links are cleaned and deduplicated", [x["id"] for x in links.clean_stored(
    {"links": [dict(a, id="aaaaaaaa"), dict(a, id="aaaaaaaa"), {"url": "javascript:x"}, "x"]})["links"]], ["aaaaaaaa"])

print("what the player is told")
web = links.clean_link({"url": "https://example.org/menu?x=1"})
r = links.resolve_web(web)
eq("a web page", (r["type"], r["url"], r["probe"]), ("page", "https://example.org/menu?x=1", "https://example.org/"))
yt = links.clean_link({"url": f"https://youtu.be/{V}"})
r = links.resolve_youtube(yt)
check("a video is a page on the privacy-friendly embed host", r["url"].startswith(
    f"https://www.youtube-nocookie.com/embed/{V}?"))
check("muted unless the screen has sound", "mute=1" in r["url"] and "mute" not in r["url_sound"])
check("a single video can end its item", r["end_when"] and r["post_message"]["event"] == "listening")
pl = links.clean_link({"url": f"https://www.youtube.com/playlist?list={L}"})
r = links.resolve_youtube(pl)
check("a playlist loops through the series", "/embed/videoseries?" in r["url"] and f"list={L}" in r["url"])
check("and runs for the item's seconds", "end_when" not in r)

print("source contract")
eq("extras survive signage's filter", sorted(sources.Source and links.resolve_youtube(yt) and
   (lambda s: (sources.register(s), sources.resolve("t", "x"))[1])(sources.Source(
       kind="t", label="t", resolve=lambda ref: links.resolve_youtube(yt))) or {}),
   ["end_when", "post_message", "probe", "type", "url", "url_sound"])
sources.unregister("t")
eq("junk extras are dropped", sources.resolve("t", "x"), None)
sources.register(sources.Source(kind="t", label="t", resolve=lambda ref: {
    "type": "page", "url": "https://x", "probe": 5, "end_when": ["no"], "post_message": "no", "url_sound": None}))
eq("bad extras are left out", sources.resolve("t", "x"), {"type": "page", "url": "https://x"})
sources.unregister("t")

print("framing headers")
eq("none means allowed", links.frame_verdict(hdrs())[0], "ok")
eq("X-Frame-Options refuses", links.frame_verdict(hdrs(X_Frame_Options="SAMEORIGIN"))[0], "blocked")
eq("frame-ancestors 'none' refuses", links.frame_verdict(hdrs(Content_Security_Policy="default-src *; frame-ancestors 'none'"))[0], "blocked")
eq("frame-ancestors 'self' refuses", links.frame_verdict(hdrs(Content_Security_Policy="frame-ancestors 'self'"))[0], "blocked")
eq("frame-ancestors * allows", links.frame_verdict(hdrs(Content_Security_Policy="frame-ancestors *"))[0], "ok")
eq("named hosts are unsure", links.frame_verdict(hdrs(Content_Security_Policy="frame-ancestors https://a.org"))[0], "unsure")


class Resp:
    def __init__(self, headers=None, body=b""):
        self.headers, self._b = headers or hdrs(), body

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def err(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "e", headers or hdrs(), io.BytesIO(b""))


answers = {}


def fake_fetch(url, headers=None):
    a = answers[url.split("?")[0]] if "oembed" not in url else answers["oembed"]
    if isinstance(a, Exception):
        raise a
    return a


links._fetch = fake_fetch

print("checks")
answers["https://example.org/menu"] = Resp(hdrs(X_Frame_Options="DENY"))
eq("a site that refuses framing", links.check(web)["state"], "blocked")
answers["https://example.org/menu"] = Resp()
eq("a site that allows it", links.check(web)["state"], "ok")
answers["https://example.org/menu"] = OSError("down")
eq("a site that can't be reached", links.check(web)["state"], "offline")
answers["oembed"] = Resp(body=json.dumps({"title": "Welcome"}).encode())
eq("an embeddable video", links.check(yt)["state"], "ok")
answers["oembed"] = err(401)
eq("embedding turned off", links.check(yt)["state"], "blocked")
answers["oembed"] = err(404)
eq("a video that's gone", links.check(yt)["state"], "gone")
answers["oembed"] = OSError("down")
eq("YouTube unreachable", links.check(yt)["state"], "offline")

print("the admin routes")
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"signage": {"enabled": True}, "signage_web": {"enabled": True}}})
_modules.attach("signage")
_modules.attach("signage_web")
admin = _db.client(app, base_url="http://relay.local:8001")
answers["https://example.org/menu"] = Resp()
answers["oembed"] = Resp(body=json.dumps({"title": "Welcome video"}).encode())
res = admin.post("/api/admin/signage-web/links", json={"url": "https://example.org/menu", "name": ""})
eq("a web page is added", (res.status_code, res.json()["kind"], res.json()["check"]["state"]), (200, "web", "ok"))
web_id = res.json()["id"]
eq("with its address as the name", res.json()["name"], "https://example.org/menu")
res = admin.post("/api/admin/signage-web/links", json={"url": f"https://youtu.be/{V}"})
eq("a video takes YouTube's title", (res.status_code, res.json()["name"]), (200, "Welcome video"))
yt_id = res.json()["id"]
res = admin.post("/api/admin/signage-web/links", json={"url": "javascript:alert(1)"})
eq("a bad address is a 400", res.status_code, 400)
eq("the list", [l["kind"] for l in admin.get("/api/admin/signage-web").json()["links"]], ["web", "youtube"])
eq("renaming", admin.put(f"/api/admin/signage-web/links/{web_id}", json={"name": "Menu"}).json()["name"], "Menu")
answers["https://example.org/menu"] = Resp(hdrs(X_Frame_Options="DENY"))
eq("checking again", admin.post(f"/api/admin/signage-web/links/{web_id}/check").json()["check"]["state"], "blocked")
eq("an unknown link is a 404", admin.post("/api/admin/signage-web/links/nope/check").status_code, 404)

print("in a playlist")


def _pl(pid):
    return [p for p in store.playlists() if p["id"] == pid][0]


eq("both sources are registered", (sources.get("web_page") is not None, sources.get("youtube") is not None), (True, True))
eq("web choices", [c["name"] for c in sources.get("web_page").choices()], ["Menu"])
eq("a single video is added as a video (plays to its end)", sources.get("youtube").choices()[0]["kind"], "video")
pid = store.playlist_create("Lobby")["id"]
store.playlist_set_items(pid, [{"source": "web_page", "ref": web_id, "duration_s": 20},
                               {"source": "youtube", "ref": yt_id, "duration_s": 0}])
items = store.playable_items(pid, store.today())
eq("both play", [i["type"] for i in items], ["page", "page"])
eq("with their seconds", [i["duration_s"] for i in items], [20, 0])
check("the video ends itself", "end_when" in items[1])
eq("the admin list names them", [i["name"] for i in _pl(pid)["items"]], ["Menu", "Welcome video"])
res = admin.delete(f"/api/admin/signage-web/links/{web_id}")
eq("removing a link", res.status_code, 200)
eq("its playlist item is skipped", len(store.playable_items(pid, store.today())), 1)

print("switched off")
config.save({"modules": {"signage_web": {"enabled": False}}})
_modules.get("signage_web").detach()
eq("the routes answer 404", admin.get("/api/admin/signage-web").status_code, 404)
eq("the items are skipped", store.playable_items(pid, store.today()), [])
eq("but stay in the playlist", len(_pl(pid)["items"]), 2)
eq("and show as missing", [i.get("missing") for i in _pl(pid)["items"]], [True, True])

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
