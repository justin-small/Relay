"""The crew page (#226): sign-in, the manifest and service worker, the plan, the tabs modules add, and where it is served.

Offline: a throwaway database and the app's own routes. Run: python tests/test_crew.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import config, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import CrewTab, modules  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": False}}})
member = _db.client(app, role="member")
operator = _db.client(app, role="operator")
anon = TestClient(app, base_url="http://relay.local:8001")
on_viewer_port = TestClient(app, base_url="http://relay.local:8000")

print("who may open it")
r = anon.get("/crew")
check("signed out, the sign-in form", r.status_code == 200 and 'action="/admin/login"' in r.text)
r = member.get("/crew")
eq("a member opens it", r.status_code, 200)
check("it greets them and links the manifest", "Member" in r.text and "/crew/manifest.webmanifest" in r.text)
check("a member gets no operator link", 'href="/admin"' not in r.text)
check("but can change the password and sign out", "/home?password=1" in r.text and "/admin/logout" in r.text)
check("a role above member may open it", operator.get("/crew").status_code == 200)
check("an operator sees the link to the panel", 'href="/admin"' in operator.get("/crew").text)
check("the permission is a member's", "crew.use" in users.role_permissions("member"))
check("the home page lists the crew page", 'href="/crew"' in member.get("/home").text)
eq("the page is not stored", member.get("/crew").headers.get("cache-control"), "no-store")
eq("it is not on the viewer port", on_viewer_port.get("/crew").status_code, 404)
eq("nor its state", on_viewer_port.get("/crew/api/state").status_code, 404)
eq("nor its manifest or worker", (on_viewer_port.get("/crew/manifest.webmanifest").status_code,
                                  on_viewer_port.get("/crew/sw.js").status_code), (404, 404))

print("installable")
r = anon.get("/crew/manifest.webmanifest")
m = r.json()
eq("the manifest needs no sign-in", r.status_code, 200)
eq("it starts and stays on the crew page", (m["start_url"], m["scope"], m["display"]), ("/crew", "/crew", "standalone"))
check("with icons, one maskable", len(m["icons"]) >= 3 and any(i.get("purpose") == "maskable" for i in m["icons"]))
check("every icon file exists", all((ROOT / "app" / i["src"].lstrip("/")).is_file() for i in m["icons"]))
r = anon.get("/crew/sw.js")
eq("the worker is javascript allowed to control /crew",
   (r.status_code, r.headers["content-type"].split(";")[0], r.headers["service-worker-allowed"]),
   (200, "text/javascript", "/crew"))
check("and is never cached by the browser", r.headers["cache-control"] == "no-cache")
check("it keeps no page or API answer", "/crew/api" not in r.text and "'/crew'" not in r.text)

print("the plan")
eq("with no plan source switched on there is no plan", member.get("/crew/api/state").json(), {"plan": None})
check("and the page says so", "No plan source" in member.get("/crew").text)
eq("signed out, the state is refused", anon.get("/crew/api/state").status_code, 401)
config.save({"modules": {"planning_center": {"enabled": True}}})
eq("with Planning Center on and no plan today", member.get("/crew/api/state").json(), {"plan": ""})
check("the page says no plan today", "No plan today" in member.get("/crew").text)
config.save({"modules": {"planning_center": {"enabled": False}}})

print("tabs from modules")
check("no tab, a short note", "Nothing is switched on for the crew" in member.get("/crew").text)
mod = modules.get("intercom")
mod.crew_tabs = (CrewTab("talk", "Intercom", "admin.html", "intercom.use"),)
mod.crew_scripts = ("intercom.js",)
check("a module that is off adds nothing", 'role="tab"' not in member.get("/crew").text)
config.save({"modules": {"intercom": {"enabled": True}}})
try:
    page = member.get("/crew").text
    check("a switched-on module adds its tab", 'id="tab-talk"' in page and 'id="panel-talk"' in page)
    check("and its script", "/static/modules/intercom/intercom.js" in page)
finally:
    mod.crew_tabs = ()
    mod.crew_scripts = ()
    config.save({"modules": {"intercom": {"enabled": False}}})

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
