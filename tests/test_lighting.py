"""Lighting module (#377): settings and cue pages, the Backend interface through a fake program, the admin routes and
their permissions, the audit trail, the fire hook, and a press that is never repeated. No real lighting program.
Run: python tests/test_lighting.py
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
from app import config, modules as registry_mod  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting import service as svc  # noqa: E402
from _harness import check, eq, fails, section  # noqa: E402


class Fake(be.Backend):
    id, label = "fake", "Fake program"
    can_list = True
    can_report_state = True
    calls: list = []
    drop = False

    async def list_cues(self):
        return [be.Cue("a", "Walk-in"), be.Cue("b", "Verse"), be.Cue("c", "Blackout")]

    async def press(self, cue_id):
        Fake.calls.append(("press", cue_id))
        if Fake.drop:
            raise ConnectionResetError("secret detail")

    async def release(self, cue_id):
        Fake.calls.append(("release", cue_id))

    async def state(self):
        return {"a": True}

    async def test(self):
        return {"ok": True, "detail": "Fake is fine."}


class Blind(be.Backend):
    id, label = "blind", "Blind program"      # cannot list (like Vista)


be.register(Fake)
be.register(Blind)


with section("settings and pages are cleaned"):
    s = svc.clean_settings({"backend": "fake", "host": " 10.0.0.5 ", "port": 70000, "password": "pw",
                            "pages": [{"name": " Worship  ", "cues": [{"id": "b", "name": "Verse"}, {"id": "b"}, {"id": "bad id"}]},
                                      {"name": ""}, "junk"]})
    eq("host trimmed, bad port dropped", (s["host"], s["port"]), ("10.0.0.5", 0))
    eq("one named page, duplicate and bad cue ids dropped", [(p["name"], p["cues"]) for p in s["pages"]],
       [("Worship", [{"id": "b", "name": "Verse"}])])
    check("a page gets an id", s["pages"][0]["id"])
    eq("junk settings are empty", svc.clean_settings("x")["backend"], "")
    check("public strips the password", svc.public(s)["password"] == "" and svc.public(s)["has_password"] is True)
    check("the module's public() agrees", _modules.get("lighting").public(s)["password"] == "")

with section("admin routes"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"lighting": {"enabled": True}}})
    _modules.attach("lighting")
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    operator = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")
    A = "/api/admin/lighting"
    asyncio.run(svc.service.start())

    r = admin.get(A)
    eq("no program yet: nothing active", (r.status_code, r.json()["active"], r.json()["cues"]), (200, False, []))
    eq("the programs are listed", [b["id"] for b in r.json()["backends"]], ["blind", "fake"])
    eq("an operator cannot choose the program", operator.put(A + "/settings", json={"backend": "fake"}).status_code, 403)
    eq("a viewer cannot fire", viewer.post(A + "/press", json={"cue": "a"}).status_code, 403)

    r = admin.put(A + "/settings", json={"backend": "fake", "host": "10.0.0.5", "port": 1234, "password": "pw"})
    eq("the program is chosen", (r.status_code, r.json()["active"], r.json()["can_list"]), (200, True, True))
    check("the password is never sent back", r.json()["password"] == "" and r.json()["has_password"] is True)
    check("but it is kept", config.get()["lighting"]["password"] == "pw")
    r = admin.put(A + "/settings", json={"host": "10.0.0.6"})
    check("an empty password leaves the saved one", config.get()["lighting"]["password"] == "pw")
    eq("a bad address is a 400", admin.put(A + "/settings", json={"host": "no spaces/please"}).status_code, 400)
    r = admin.get(A)
    eq("listed cues and state", ([c["name"] for c in r.json()["cues"]], r.json()["state"]),
       (["Walk-in", "Verse", "Blackout"], {"a": True}))
    eq("the connection test", admin.post(A + "/test").json(), {"ok": True, "detail": "Fake is fine."})

    eq("pages must be a list", admin.put(A + "/pages", json={}).status_code, 400)
    r = admin.put(A + "/pages", json={"pages": [{"name": "Worship", "cues": [{"id": "b", "name": "Verse"}]}]})
    eq("pages are saved", [p["name"] for p in r.json()["pages"]], ["Worship"])

    Fake.calls.clear()
    eq("an operator fires a cue on a page", operator.post(A + "/press", json={"cue": "b"}).status_code, 200)
    eq("the admin fires an unassigned listed cue", admin.post(A + "/press", json={"cue": "c"}).status_code, 200)
    eq("an unknown cue is 404", admin.post(A + "/press", json={"cue": "zzz"}).status_code, 404)
    eq("no cue is a 400", admin.post(A + "/press", json={}).status_code, 400)
    eq("release", admin.post(A + "/release", json={"cue": "b"}).status_code, 200)
    eq("the program was told once each", Fake.calls, [("press", "b"), ("press", "c"), ("release", "b")])

with section("audit trail"):
    from app import audit
    texts = [e["summary"] for e in audit.search(kind="lighting")["entries"]]
    check("a press is recorded with the page and the cue", "Lighting: Worship, Verse" in texts, texts)

with section("the fire hook"):
    Fake.calls.clear()
    eq("fire reaches the module through the registry", asyncio.run(registry_mod.fire_cue("b")), {"ok": True})
    Fake.calls.clear()
    r = asyncio.run(registry_mod.fire_cue("c"))
    eq("a cue not on a page cannot be fired", (r["ok"], Fake.calls), (False, []))
    config.save({"lighting": {**config.get()["lighting"], "backend": "blind"}})
    asyncio.run(svc.service.restart())
    admin.put(A + "/pages", json={"pages": [{"name": "Hand", "cues": [{"id": "g1", "name": "Hand cue"}]}]})
    r = admin.get(A).json()
    eq("a program that cannot list shows no cues but keeps its pages", (r["can_list"], r["cues"], len(r["pages"])), (False, [], 1))
    eq("a hand-added cue can be fired from the page", operator.post(A + "/press", json={"cue": "g1"}).status_code, 502)
    config.save({"lighting": {**config.get()["lighting"], "backend": "fake"}})
    asyncio.run(svc.service.restart())

with section("a dropped connection is never retried"):
    Fake.calls.clear()
    Fake.drop = True
    r = operator.post(A + "/press", json={"cue": "b"})
    eq("the operator is told", (r.status_code, "may or may not" in r.json()["error"]), (502, True))
    check("and the error does not leak the library's text", "secret detail" not in r.text)
    eq("the press was tried exactly once", Fake.calls, [("press", "b")])
    Fake.drop = False

with section("health"):
    eq("ready", _modules.get("lighting").health().state, "ok")
    config.save({"lighting": {**config.get()["lighting"], "backend": ""}})
    asyncio.run(svc.service.restart())
    eq("no program is a warning", _modules.get("lighting").health().state, "warn")

with section("switched off"):
    asyncio.run(svc.service.stop())
    config.save({"modules": {"lighting": {"enabled": False}}})
    _modules.get("lighting").detach()
    eq("the routes answer 404", admin.get(A).status_code, 404)
    eq("the hook says lighting is off", asyncio.run(registry_mod.fire_cue("b"))["ok"], False)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
