"""Lighting cue pages on /screen (#379): button looks, the per-page key, the page and its presses on the plain
viewer port, rate limits, and what a screen is told. A fake lighting program, no real one.
Run: python tests/test_lighting_screen.py
"""
import asyncio
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
from app import audit, config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting import routes as rt  # noqa: E402
from app.modules.lighting import service as svc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from _harness import check, eq, fails, section  # noqa: E402


class Fake(be.Backend):
    id, label = "fakescreen", "Fake program"
    can_report_state = True
    calls: list = []
    drop = False

    async def press(self, cue_id):
        Fake.calls.append(("press", cue_id))
        if Fake.drop:
            raise ConnectionResetError("secret detail")

    async def state(self):
        return {"a": True, "zzz": True}


be.register(Fake)

with section("a button's look is cleaned"):
    c = svc.clean_style({"label": "  Walk  in ", "shape": "triangle", "color": "red", "text_color": "#ABCDEF",
                         "w": 99, "h": 0, "x": 90, "y": -5, "fw": 30, "fh": "x"})
    eq("unknown shape, bad colour and out-of-range numbers fall back", (c["shape"], c["color"], c["text_color"], c["w"], c["h"]),
       ("rounded", "#2b5fd9", "#abcdef", 6, 1))
    eq("the label is tidied and a free box stays inside the page", (c["label"], c["x"], c["y"], c["fw"], c["fh"]),
       ("Walk in", 70, 0, 30, 20))
    check("a colour can never carry css", "{" not in svc.clean_style({"color": "#fff;}x{"})["color"])

with section("a page's key"):
    pages = svc.clean_pages([{"id": "p1", "name": "Worship", "key": "a" * 32, "key_on": True,
                              "cues": [{"id": "a", "name": "Walk-in"}]}])
    eq("a valid key is kept", (pages[0]["key"], pages[0]["key_on"]), ("a" * 32, True))
    eq("a bad key is dropped, and with it the switch", [(p["key"], p["key_on"]) for p in svc.clean_pages(
        [{"id": "p1", "name": "W", "key": "short", "key_on": True}])], [("", False)])
    eq("what an editor sends cannot set the key", svc.clean_pages([{"id": "p1", "name": "W", "key": "b" * 32}], {"p1": "c" * 32})[0]["key"], "c" * 32)
    check("the key is 128 bits of hex", len(svc.new_key()) == 32 and svc.new_key() != svc.new_key())
    check("public never carries a key", all("key" not in p and p["has_key"] for p in svc.public({"pages": pages})["pages"]))

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"lighting": {"enabled": True}}})
_modules.attach("lighting")
asyncio.run(svc.service.start())
admin = _db.client(app, base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")
A = "/api/admin/lighting"
admin.put(A + "/settings", json={"backend": "fakescreen"})
r = admin.put(A + "/pages", json={"pages": [
    {"id": "p1", "name": "Worship", "cues": [{"id": "a", "name": "Walk-in", "label": "WALK IN", "shape": "circle", "color": "#112233"},
                                              {"id": "b", "name": "Verse"}]},
    {"id": "p2", "name": "Kids", "free": True, "cues": [{"id": "c", "name": "Games", "x": 10, "y": 10, "fw": 40, "fh": 30}]}]})
eq("two pages saved, each with its own cues", [(p["id"], [c["id"] for c in p["cues"]]) for p in r.json()["pages"]],
   [("p1", ["a", "b"]), ("p2", ["c"])])
eq("a look is saved", (r.json()["pages"][0]["cues"][0]["shape"], r.json()["pages"][0]["cues"][0]["label"]), ("circle", "WALK IN"))
tv = TestClient(app, base_url="http://relay.local:8000")      # a tablet: the plain viewer port, no sign-in

with section("the screen is off until switched on"):
    eq("a page with no key is not found", tv.get("/screen?lighting=p1&key=" + "a" * 32).status_code, 404)
    eq("an operator cannot switch it on", operator.put(f"{A}/pages/p1/screen", json={"on": True}).status_code, 403)
    eq("an unknown page is 404", admin.put(f"{A}/pages/nope/screen", json={"on": True}).status_code, 404)
    eq("a key cannot be rotated while off", admin.put(f"{A}/pages/p1/screen", json={"on": False, "rotate": True}).status_code, 400)
    r = admin.put(f"{A}/pages/p1/screen", json={"on": True})
    link = r.json()["link"]
    check("the link names the page and a 128-bit key", "/screen?lighting=p1&key=" in link and len(link.split("key=")[1]) == 32, link)
    KEY1 = link.split("key=")[1]
    admin.put(f"{A}/pages/p2/screen", json={"on": True})
    KEY2 = admin.get(f"{A}/pages/p2/link").json()["link"].split("key=")[1]
    check("each page has its own key", KEY1 != KEY2)
    check("a page save does not touch keys", admin.put(A + "/pages", json={"pages": admin.get(A).json()["pages"]}).status_code == 200
          and admin.get(f"{A}/pages/p1/link").json()["link"].endswith(KEY1))
    check("no read of the settings carries a key", KEY1 not in admin.get(A).text and KEY1 not in str(svc.public(config.get()["lighting"])))
    eq("an operator cannot read the link", operator.get(f"{A}/pages/p1/link").status_code, 403)

with section("the page"):
    r = tv.get(f"/screen?lighting=p1&key={KEY1}")
    eq("it opens with the right key", r.status_code, 200)
    check("it holds this page's buttons, not another's", "WALK IN" in r.text and "Games" not in r.text and "ltgrid" in r.text)
    check("the free page is drawn free", "ltfree" in tv.get(f"/screen?lighting=p2&key={KEY2}").text)
    eq("another page's key does not open it", tv.get(f"/screen?lighting=p1&key={KEY2}").status_code, 404)
    eq("no key", tv.get("/screen?lighting=p1").status_code, 404)
    eq("a wrong key is the same plain 404 as a missing page", (tv.get("/screen?lighting=p1&key=bad").text, tv.get("/screen?lighting=zz&key=bad").text),
       ("Not found", "Not found"))
    check("the page is not cached and sends no referrer", r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer")
    check("/screen still serves captions", tv.get("/screen").status_code == 200 and "ltScreen" not in tv.get("/screen").text)

with section("pressing"):
    audit_before = len(audit.search(kind="lighting")["entries"])
    Fake.calls.clear()
    P = f"/lighting/_/press/p1?key={KEY1}"
    eq("a tap fires the cue once", (tv.post(P, json={"cue": "a"}).status_code, Fake.calls), (200, [("press", "a")]))
    texts = [e["summary"] for e in audit.search(kind="lighting")["entries"]]
    check("it is logged with the page and where it came from", "Lighting: Worship, Walk-in (from the Worship screen)" in texts, texts)
    Fake.calls.clear()
    r = tv.post(f"/lighting/_/press/p1?key={KEY1}", json={"cue": "c"})
    eq("a cue on another page is refused, and nothing is sent", (r.status_code, r.json(), Fake.calls), (404, {"error": "refused"}, []))
    eq("no cue", tv.post(P, json={}).json(), {"error": "refused"})
    eq("a wrong key presses nothing", (tv.post("/lighting/_/press/p1?key=" + "0" * 32, json={"cue": "a"}).status_code, Fake.calls), (404, []))
    eq("the screen being off stops it", (tv.post(f"/lighting/_/press/p3?key={KEY1}", json={"cue": "a"}).status_code), 404)
    entries = audit.search(kind="lighting")["entries"]
    eq("refused taps are not logged", len(entries), audit_before + 1)

with section("what a screen is told"):
    Fake.drop = True
    r = tv.post(P, json={"cue": "b"})
    eq("only a short code", (r.status_code, r.json()), (502, {"error": "unreachable"}))
    check("never the program's text", "secret" not in r.text)
    Fake.drop = False
    asyncio.run(svc.service._close())
    eq("no program is unreachable too", tv.post(P, json={"cue": "b"}).json(), {"error": "unreachable"})
    svc.service._open()
    r = tv.get(f"/lighting/_/state/p1?key={KEY1}")
    eq("state lists only this page's cues", r.json(), {"state": {"a": True, "b": False}})
    eq("state needs the key too", tv.get("/lighting/_/state/p1?key=nope").status_code, 404)

with section("rate limits"):
    rt._hits.clear(); rt._bad_hits.clear()
    Fake.calls.clear()
    codes = [tv.post(P, json={"cue": "a"}).status_code for _ in range(rt.MAX_PRESSES + 3)]
    eq("a flood of taps is cut off", (codes.count(200), codes[-1]), (rt.MAX_PRESSES, 429))
    eq("the program saw only the allowed taps", len(Fake.calls), rt.MAX_PRESSES)
    eq("the screen is told to slow down", tv.post(P, json={"cue": "a"}).json(), {"error": "slow"})
    rt._hits.clear(); rt._bad_hits.clear()
    before = len(audit.search(kind="lighting")["entries"])
    codes = [tv.get("/screen?lighting=p1&key=" + format(i, "032x")).status_code for i in range(rt.MAX_BAD_KEYS + 4)]
    eq("guessing keys is slowed down", (codes[0], codes[-1]), (404, 429))
    eq("and the right key waits too", tv.get(f"/screen?lighting=p1&key={KEY1}").status_code, 429)
    eq("guesses are never logged", len(audit.search(kind="lighting")["entries"]), before)
    rt._hits.clear(); rt._bad_hits.clear()

with section("rotating and turning off"):
    r = admin.put(f"{A}/pages/p1/screen", json={"on": True, "rotate": True})
    NEW = r.json()["link"].split("key=")[1]
    eq("the old link stops at once", tv.get(f"/screen?lighting=p1&key={KEY1}").status_code, 404)
    rt._bad_hits.clear()
    eq("the new link works", tv.get(f"/screen?lighting=p1&key={NEW}").status_code, 200)
    eq("turning off", admin.put(f"{A}/pages/p1/screen", json={"on": False}).json(), {"on": False, "link": ""})
    eq("the link is gone", admin.get(f"{A}/pages/p1/link").json(), {"on": False, "link": ""})
    eq("and the page", tv.get(f"/screen?lighting=p1&key={NEW}").status_code, 404)
    texts = [e["summary"] for e in audit.search(kind="lighting")["entries"]]
    check("switching is in the audit trail", any("Turned off the lighting screen for Worship" in t for t in texts), texts)

with section("switched off"):
    asyncio.run(svc.service.stop())
    config.save({"modules": {"lighting": {"enabled": False}}})
    _modules.get("lighting").detach()
    rt._bad_hits.clear()
    check("/screen?lighting= falls back to the captions page", "ltScreen" not in tv.get(f"/screen?lighting=p2&key={KEY2}").text)
    eq("the press route answers 404", tv.post(f"/lighting/_/press/p2?key={KEY2}", json={"cue": "c"}).status_code, 404)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
