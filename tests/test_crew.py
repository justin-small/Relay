"""The crew web app (#226): the page is for a signed-in crew account, only on the HTTPS port, installable
(manifest and service worker), and its plan and settings follow permissions. Run: python tests/test_crew.py
"""
import os
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

import app.main as main  # noqa: E402,F401
from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.crew import MODULE  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"crew": {"enabled": False}}})
admin = _db.client(app, base_url="http://relay.local:8001")
member = _db.client(app, role="member", base_url="http://relay.local:8001", username="crew1")
viewer_port = TestClient(app, base_url="http://relay.local:8000")
anon = TestClient(app, base_url="http://relay.local:8001")

print("off")
eq("off: the page is gone", admin.get("/crew").status_code, 404)
eq("off: the manifest is gone", anon.get("/crew/_/manifest.webmanifest").status_code, 404)
eq("off: not on the home page", 'href="/crew"' in member.get("/home").text, False)

config.save({"modules": {"crew": {"enabled": True}}})
print("settings")
eq("defaults", config.get()["crew"], {"name": "Relay crew", "show_plan": True})
eq("a hand edit is cleaned", MODULE.clean({"name": "  A   very long name that goes on and on ", "show_plan": "x"}),
   {"name": "A very long name that go", "show_plan": True})
eq("an empty name is the default", MODULE.clean({"name": "  "})["name"], "Relay crew")

print("the page")
eq("a member sees it linked on the home page", 'href="/crew"' in member.get("/home").text, True)
r = member.get("/crew")
eq("a member opens the page", (r.status_code, "Relay crew" in r.text, 'rel="manifest"' in r.text), (200, True, True))
eq("it is never stored", r.headers.get("cache-control"), "no-store")
r = anon.get("/crew")
eq("signed out gets the sign-in form", (r.status_code, "password" in r.text.lower(), "Relay crew" in r.text), (200, True, False))
eq("the viewer port refuses the page", viewer_port.get("/crew").status_code, 404)
eq("the viewer port refuses the plan", viewer_port.get("/crew/api/plan").status_code, 404)
eq("the viewer port refuses the manifest", viewer_port.get("/crew/_/manifest.webmanifest").status_code, 404)
eq("the viewer port refuses the service worker", viewer_port.get("/crew/_/sw.js").status_code, 404)

print("installable")
r = anon.get("/crew/_/manifest.webmanifest")
m = r.json()
eq("the manifest needs no sign-in", (r.status_code, r.headers["content-type"].split(";")[0]),
   (200, "application/manifest+json"))
eq("it scopes to the page and installs standalone", (m["start_url"], m["scope"], m["display"], m["name"]),
   ("/crew", "/crew", "standalone", "Relay crew"))
eq("it has the icons an install needs", sorted(i["sizes"] for i in m["icons"]), ["192x192", "512x512", "512x512"])
r = anon.get("/crew/_/sw.js")
eq("the service worker is widened to the page", (r.status_code, r.headers["service-worker-allowed"],
                                                 r.headers["content-type"].split(";")[0]),
   (200, "/crew", "text/javascript"))
eq("it keeps only its own files and falls back to the offline page",
   ("SHELL.indexOf(url.pathname)" in r.text, "caches.match(OFFLINE)" in r.text), (True, True))
eq("the offline page needs no sign-in", (anon.get("/crew/_/offline").status_code), 200)

print("the plan")
eq("a member reads the plan route", member.get("/crew/api/plan").json(), {"available": False, "plan": None})
eq("signed out is refused", anon.get("/crew/api/plan").status_code, 401)

print("settings routes")
eq("a member may not read the setup", member.get("/api/admin/crew").status_code, 403)
eq("an admin reads it", admin.get("/api/admin/crew").json(), {"name": "Relay crew", "show_plan": True})
eq("an empty name is refused", admin.post("/api/admin/crew", json={"name": " ", "show_plan": True}).status_code, 400)
eq("a non-boolean is refused", admin.post("/api/admin/crew", json={"name": "A", "show_plan": "yes"}).status_code, 400)
r = admin.post("/api/admin/crew", json={"name": "Booth crew", "show_plan": False})
eq("saved", (r.status_code, r.json()), (200, {"name": "Booth crew", "show_plan": False}))
eq("the manifest follows the name", anon.get("/crew/_/manifest.webmanifest").json()["name"], "Booth crew")
eq("the page follows it", "Booth crew" in member.get("/crew").text and 'id="crPlan"' not in member.get("/crew").text, True)

print("tabs from modules (#240)")
from app.modules import CrewTab, modules as registry  # noqa: E402

page = member.get("/crew").text
eq("no other module's tab: only the crew's own chat", ('data-tab="intercom"' in page, 'data-tab="chat"' in page), (False, True))
other = registry.get("intercom")
config.save({"modules": {"intercom": {"enabled": True}}})
page = member.get("/crew").text
eq("a switched-on module adds its tab, panel and scripts in order",
   ('id="tab-intercom"' in page, 'id="icTalk"' in page,
    page.index("modules/intercom/livekit-client.umd.js") < page.index("modules/intercom/crew.js")),
   (True, True, True))
eq("the intercom module declares its tab for people who may use it",
   [(t.id, t.permission) for t in other.crew_tabs], [("intercom", "intercom.use")])
eq("the library is vendored with its licence, no CDN",
   all((Path(__file__).resolve().parent.parent / "app/modules/intercom/static" / f).is_file()
       for f in ("livekit-client.umd.js", "livekit-client.LICENSE.txt")) and "cdn" not in page.lower(), True)
eq("the microphone is allowed on the crew page only while the intercom is on",
   ("microphone=(self)" in member.get("/crew").headers["permissions-policy"],
    "microphone=()" in member.get("/home").headers["permissions-policy"]), (True, True))
config.save({"modules": {"intercom": {"enabled": False}}})
eq("off again, the tab and the microphone are gone",
   ('data-tab="intercom"' in member.get("/crew").text, "microphone=()" in member.get("/crew").headers["permissions-policy"]),
   (False, True))
mine = other.crew_tabs, other.crew_scripts
other.crew_tabs = (CrewTab("t", "T", "crew.html", "nope.use"),)
from app.modules import validate  # noqa: E402

eq("a tab needs one of the module's own permissions",
   any("crew tab" in x for x in validate(other, "intercom")), True)
other.crew_tabs, other.crew_scripts = mine

if fails:
    print(f"\n{len(fails)} failed")
    sys.exit(1)
print("\nall passed")
