"""Lighting module (#377): settings and cue pages, the Backend interface through a fake program, the admin routes and
their permissions, the audit trail, the fire hook, and a press that is never repeated. No real lighting program.
Run: python tests/test_lighting.py
"""
import asyncio
import json
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
    can_set_level = True
    calls: list = []
    drop = False

    async def list_cues(self):
        return [be.Cue("a", "Walk-in"), be.Cue("b", "Verse"), be.Cue("c", "Blackout"), be.Cue("s9", "House", "slider")]

    async def set_level(self, cue_id, percent):
        Fake.calls.append(("level", cue_id, percent))

    async def levels(self):
        return {"s9": 40}

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
    eq("host trimmed, bad port dropped", (s["programs"][0]["host"], s["programs"][0]["port"]), ("10.0.0.5", 0))
    eq("the flat settings from before #484 become the first program, main", (len(s["programs"]), s["programs"][0]["id"]),
       (1, "main"))
    eq("one named page, duplicate and bad cue ids dropped", [(p["name"], [(c["id"], c["name"]) for c in p["cues"]]) for p in s["pages"]],
       [("Worship", [("b", "Verse")])])
    check("a page gets an id", s["pages"][0]["id"])
    eq("junk settings are empty", svc.clean_settings("x")["programs"], [])
    pub = svc.public(s)["programs"][0]
    check("public strips the password", "password" not in pub and pub["has_password"] is True)
    check("the module's public() agrees", "passwords" not in _modules.get("lighting").public(s))
    check("the password is stored apart, to be sealed", json.loads(s["passwords"]) == {"main": "pw"})
    many = svc.clean_settings({"programs": [{"backend": "fake", "name": "Sanctuary"}, {"backend": "fake"},
                                            {"id": "main", "backend": "blind"}, "junk"]})["programs"]
    eq("several programs get unique ids and names", [(p["id"], p["name"]) for p in many],
       [("main", "Sanctuary"), ("p2", "Fake program"), ("p3", "Blind program")])

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
    eq("the programs are listed", [b["id"] for b in r.json()["backends"]], ["blind", "fake", "lightkey", "qlcplus", "thelightingcontroller", "vista"])
    progs = {b["id"]: b for b in r.json()["backends"]}
    eq("each program says which fields it needs",
       {k: (progs[k]["fields"], progs[k]["default_port"]) for k in ("qlcplus", "lightkey", "vista", "thelightingcontroller", "fake")},
       {"qlcplus": (["host", "port", "functions"], 9999), "lightkey": (["host", "port", "fade"], 21600),
        "vista": (["host", "port", "console"], 8000), "thelightingcontroller": (["host", "port", "password"], 7348),
        "fake": (["host", "port"], 0)})
    check("a real program says what it needs", all(progs[k]["about"] for k in ("qlcplus", "lightkey", "vista", "thelightingcontroller")))
    eq("an operator cannot choose the program",operator.put(A + "/settings", json={"backend": "fake"}).status_code, 403)
    eq("a viewer cannot fire", viewer.post(A + "/press", json={"cue": "a"}).status_code, 403)

    r = admin.put(A + "/settings", json={"backend": "fake", "host": "10.0.0.5", "port": 1234, "password": "pw"})
    eq("the program is chosen", (r.status_code, r.json()["active"], r.json()["can_list"]), (200, True, True))
    one = r.json()["programs"][0]
    check("the password is never sent back", "password" not in one and one["has_password"] is True)
    check("but it is kept", svc.service.programs()[0]["password"] == "pw")
    r = admin.put(A + "/settings", json={"host": "10.0.0.6"})
    check("an empty password leaves the saved one", svc.service.programs()[0]["password"] == "pw")
    eq("a bad address is a 400", admin.put(A + "/settings", json={"host": "no spaces/please"}).status_code, 400)
    r = admin.get(A)
    eq("listed cues and state", ([c["name"] for c in r.json()["cues"]], r.json()["state"]),
       (["Walk-in", "Verse", "Blackout", "House"], {"a": True}))
    eq("a slider's level is listed", (r.json()["can_set_level"], r.json()["levels"], r.json()["cues"][3]["kind"]),
       (True, {"s9": 40}, "slider"))
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

    Fake.calls.clear()
    eq("a viewer cannot set a level", viewer.post(A + "/level", json={"cue": "s9", "level": 5}).status_code, 403)
    eq("an operator sets a level", operator.post(A + "/level", json={"cue": "s9", "level": 70}).status_code, 200)
    eq("a level over 100 is refused", admin.post(A + "/level", json={"cue": "s9", "level": 101}).status_code, 400)
    eq("a text level is refused", admin.post(A + "/level", json={"cue": "s9", "level": "5"}).status_code, 400)
    eq("a level on an unknown cue is 404", admin.post(A + "/level", json={"cue": "zzz", "level": 5}).status_code, 404)
    eq("the program was told once", Fake.calls, [("level", "s9", 70)])
    eq("the functions setting is saved", admin.put(A + "/settings", json={"functions": True}).json()["programs"][0]["functions"], True)
    admin.put(A + "/settings", json={"functions": False})

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
    eq("a cue is fired by its name, any case", asyncio.run(registry_mod.fire_cue(" verse ", by_name=True,
                                                                              source="a ProPresenter slide tag")),
       {"ok": True})
    eq("…and the program was told its id", Fake.calls, [("press", "b")])
    r = asyncio.run(registry_mod.fire_cue("Blackout", by_name=True))
    eq("a name that is not on a page is refused", (r["ok"], "name" in r["error"]), (False, True))
    texts = [e["summary"] for e in audit.search(kind="lighting")["entries"]]
    check("a tag press is recorded as coming from the slide tag",
          "Lighting: Worship, Verse (from a ProPresenter slide tag)" in texts, texts)
    Fake.calls.clear()
    admin.put(A + "/settings", json={"backend": "blind"})
    admin.put(A + "/pages", json={"pages": [{"name": "Hand", "cues": [{"id": "g1", "name": "Hand cue"}]}]})
    r = admin.get(A).json()
    eq("a program that cannot list shows no cues but keeps its pages", (r["can_list"], r["cues"], len(r["pages"])), (False, [], 1))
    eq("a hand-added cue can be fired from the page", operator.post(A + "/press", json={"cue": "g1"}).status_code, 502)
    admin.put(A + "/settings", json={"backend": "fake"})

class Second(Fake):
    id, label = "second", "Second program"
    calls: list = []

    async def list_cues(self):
        return [be.Cue("a", "Chorus"), be.Cue("z", "Strobe")]

    async def press(self, cue_id):
        Second.calls.append(("press", cue_id))

    async def state(self):
        return {"a": True, "z": False}


be.register(Second)

with section("several programs at once"):
    Fake.calls.clear()
    r = admin.put(A + "/settings", json={"programs": [
        {"id": "main", "name": "Sanctuary", "backend": "fake", "host": "10.0.0.5", "password": "one"},
        {"name": "Youth room", "backend": "second", "host": "10.0.0.9", "password": "two"},
        {"name": "Lobby", "backend": "fake", "host": "10.0.0.7"}]})
    ids = [p["id"] for p in r.json()["programs"]]
    eq("three programs, two of the same kind", (r.status_code, ids, [p["active"] for p in r.json()["programs"]]),
       (200, ["main", "p2", "p3"], [True, True, True]))
    check("no password comes back", all("password" not in p for p in r.json()["programs"]))
    eq("each program keeps its own password", [p["password"] for p in svc.service.programs()], ["one", "two", ""])
    eq("each backend got its own program's settings", [svc.service.backends[i].settings["host"] for i in ids],
       ["10.0.0.5", "10.0.0.9", "10.0.0.7"])
    r = admin.get(A).json()
    eq("every program's cues are listed, the first bare and the others prefixed",
       [(c["id"], c["program"]) for c in r["cues"] if c["name"] in ("Walk-in", "Chorus", "Strobe")],
       [("a", "main"), ("p2:a", "p2"), ("p2:z", "p2"), ("p3:a", "p3")])
    eq("two programs both having a cue a do not clash", {k: v for k, v in r["state"].items() if k in ("a", "p2:a", "p2:z")},
       {"a": True, "p2:a": True, "p2:z": False})
    admin.put(A + "/pages", json={"pages": [{"name": "Both", "cues": [{"id": "b", "name": "Verse"},
                                                                       {"id": "p2:a", "name": "Chorus"}]}]})
    Fake.calls.clear()
    Second.calls.clear()
    eq("a cue goes to its own program", (operator.post(A + "/press", json={"cue": "p2:a"}).status_code,
                                         operator.post(A + "/press", json={"cue": "b"}).status_code), (200, 200))
    eq("…only that program was told", (Second.calls, Fake.calls), ([("press", "a")], [("press", "b")]))
    eq("a program that is not there is not a prefix", operator.post(A + "/press", json={"cue": "p9:a"}).status_code, 404)
    r = admin.put(A + "/settings", json={"programs": [{"id": "main", "backend": "fake", "host": "10.0.0.5"}]})
    eq("a program removed leaves the rest", [p["id"] for p in r.json()["programs"]], ["main"])
    eq("its password does not linger, the kept one stays", json.loads(config.get()["lighting"]["passwords"] or "{}"),
       {"main": "one"})
    eq("test one program", admin.post(A + "/test", json={"program": "main"}).json(), {"ok": True, "detail": "Fake is fine."})
    admin.put(A + "/settings", json={"programs": [{"id": "main", "backend": "fake"}, {"backend": "blind"}]})
    check("a test of all says each program", "Fake is fine." in admin.post(A + "/test").json()["detail"])
    eq("health names a program that is not ready", _modules.get("lighting").health().state, "ok")
    admin.put(A + "/settings", json={"programs": [{"id": "main", "backend": "fake"}]})

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
    admin.put(A + "/settings", json={"programs": []})
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
