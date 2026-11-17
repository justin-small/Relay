"""Boards (#156): the store, who may open, change and press what on which port, widget settings checked against their
definitions, a module's widget (lighting) through the registry, export and import. A fake lighting program.
Run: python tests/test_boards.py
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
from app import audit, boards, config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting import service as svc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402


class Fake(be.Backend):
    id, label = "fakeboard", "Fake program"
    can_report_state = True
    calls: list = []

    async def press(self, cue_id):
        Fake.calls.append(cue_id)

    async def state(self):
        return {"walk": True}


be.register(Fake)

with section("stored boards are bounded on load"):
    got = boards.clean_list([{"id": "lobby", "name": "Lobby"}, {"id": "lobby", "name": "Again"}, {"id": "Bad Id"},
                             {"id": "x", "name": "", "ratio": "21:9", "items": [{"type": "lighting.cue", "props": {"cue": "a", "n": [1]}},
                                                                                 {"type": "nope!"}]}, "junk"])
    eq("one per address, bad addresses dropped", [b["id"] for b in got], ["lobby", "x"])
    eq("a board with no name or shape gets defaults", (got[1]["name"], got[1]["ratio"], got[1]["cols"], got[1]["rows"]),
       ("Board", "16:9", 12, 8))
    eq("a module widget is kept with plain settings only", got[1]["items"][0]["props"], {"cue": "a", "n": None})
    eq("an item with a bad type is dropped", len(got[1]["items"]), 1)

with section("a module's widget definition"):
    check("its type must start with the module's id", boards.clean_definition({"type": "obs.scene"}, owner="lighting") is None)
    check("core types have no dot", boards.clean_definition({"type": "a.b"}) is None)
    d = boards.clean_definition({"type": "lighting.cue", "kind": "rocket", "w": 99,
                                 "fields": [{"key": "Bad Key", "type": "text"}, {"key": "cue", "type": "select", "options": [["a", "A"], "x"]}]},
                                owner="lighting")
    eq("unknown kind falls back, sizes bounded, bad fields dropped", (d["kind"], d["w"], [f["key"] for f in d["fields"]],
                                                                       d["fields"][0]["options"]), ("text", 24, ["cue"], [["a", "A"]]))

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"lighting": {"enabled": True}}})
_modules.attach("lighting")
_modules._running.add("lighting")
asyncio.run(svc.service.start())
admin = _db.client(app, base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", username="op1", base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="view1", base_url="http://relay.local:8001")
member = _db.client(app, role="member", username="mem1", base_url="http://relay.local:8001")
nobody = TestClient(app, base_url="http://relay.local:8001")       # the operator port, signed out
tv = TestClient(app, base_url="http://relay.local:8000")           # the plain viewer port
B = "/api/admin/boards"
admin.put("/api/admin/lighting/settings", json={"backend": "fakeboard"})
admin.put("/api/admin/lighting/pages", json={"pages": [{"id": "p1", "name": "Worship", "cues": [
    {"id": "walk", "name": "Walk-in", "style": {"bg": "#7c3aed"}}, {"id": "msg", "name": "Message"}]}]})

with section("making boards"):
    eq("a member cannot make one", member.post(B, json={"name": "Lobby"}).status_code, 403)
    eq("a name is needed", admin.post(B, json={"name": "  "}).json()["error"], "Give the board a name.")
    r = admin.post(B, json={"name": "Lobby TV", "ratio": "4:3"})
    eq("its address comes from its name, its grid from its shape", (r.json()["saved"], r.json()["boards"][0]["cols"],
                                                                   r.json()["boards"][0]["rows"]), ("lobby-tv", 8, 6))
    eq("a second board of the same name gets its own address", admin.post(B, json={"name": "Lobby TV"}).json()["saved"], "lobby-tv-2")
    cat = {d["type"]: d for d in admin.get(B).json()["catalogue"]}
    check("core widgets and the lighting cue are offered", {"captions", "clock", "countdown", "text", "link", "stream",
                                                            "lighting.cue"} <= set(cat))
    eq("the cue widget offers the cues on cue pages", [p["name"] for p in cat["lighting.cue"]["presets"]], ["Walk-in", "Message"])
    eq("the board link offers the boards", [o[0] for o in cat["link"]["fields"][0]["options"]], ["lobby-tv", "lobby-tv-2"])
    eq("a viewer may list them", viewer.get(B).status_code, 200)

with section("laying a board out"):
    r = admin.put(f"{B}/lobby-tv", json={"items": [
        {"id": "cap", "type": "captions", "x": 0, "y": 0, "w": 8, "h": 3, "props": {"lang": "KLINGON", "lines": "9"}},
        {"id": "clk", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 2, "props": {"h24": "yes"}},
        {"id": "cd", "type": "countdown", "x": 0, "y": 3, "w": 2, "h": 1, "props": {"at": "25:99"}},
        {"id": "txt", "type": "text", "x": 2, "y": 3, "w": 3, "h": 1, "props": {"text": "Hi\n  there  " + "x" * 500, "evil": 1}},
        {"id": "cue", "type": "lighting.cue", "x": 5, "y": 3, "w": 2, "h": 1, "props": {"cue": "walk"},
         "style": {"bg": "url(javascript:x)", "label": "<b>Walk</b>"}},
        {"id": "lnk", "type": "link", "x": 0, "y": 4, "w": 2, "h": 1, "props": {"board": "lobby-tv-2"}},
        {"id": "bad", "type": "marquee", "x": 0, "y": 5}]})
    eq("saved", r.status_code, 200)
    items = {i["id"]: i for i in r.json()["board"]["items"]}
    eq("an unknown core widget is dropped", "bad" in items, False)
    eq("a language that is not offered falls back to the original speech", items["cap"]["props"], {"lang": "SOURCE", "lines": "3"})
    eq("an overlapping widget moves to the first free cells", (items["clk"]["x"], items["clk"]["y"]), (0, 3))
    cells = [(i["x"] + dx, i["y"] + dy) for i in items.values() for dx in range(i["w"]) for dy in range(i["h"])]
    eq("nothing overlaps", len(cells), len(set(cells)))
    eq("a bad time falls back", items["cd"]["props"]["at"], "10:30")
    check("text is kept as plain lines, cut, and unknown settings dropped",
          items["txt"]["props"] == {"text": items["txt"]["props"]["text"]} and items["txt"]["props"]["text"].startswith("Hi\nthere")
          and len(items["txt"]["props"]["text"]) <= boards.TEXT_MAX)
    eq("a colour cannot carry anything but a colour", items["cue"]["style"]["bg"], "#2b5fd9")
    eq("a label stays text (the page sets it as text)", items["cue"]["style"]["label"], "<b>Walk</b>")
    eq("a link to another board is kept", items["lnk"]["props"], {"board": "lobby-tv-2"})
    eq("an operator cannot change it", operator.put(f"{B}/lobby-tv", json={"name": "X"}).status_code, 403)
    eq("the address never changes with the name", admin.put(f"{B}/lobby-tv", json={"name": "Front lobby"}).json()["board"]["id"],
       "lobby-tv")

with section("who opens a board"):
    eq("signed in on the operator port: it opens", member.get("/board/lobby-tv").status_code, 200)
    r = nobody.get("/board/lobby-tv")
    check("signed out: the sign-in form, not the board", "boardData" not in r.text)
    eq("the viewer port: not found", tv.get("/board/lobby-tv").status_code, 404)
    eq("an unknown board: not found", member.get("/board/nope").status_code, 404)
    eq("its data needs the same", (tv.get("/board/lobby-tv/_/data").status_code, nobody.get("/board/lobby-tv/_/data").status_code),
       (404, 404))
    r = admin.put(f"{B}/lobby-tv", json={"public": True})
    eq("made public", r.json()["board"]["public"], True)
    eq("a public board opens on the viewer port", tv.get("/board/lobby-tv").status_code, 200)
    check("and signed out on the operator port", "boardData" in nobody.get("/board/lobby-tv").text)
    r = member.get("/board/lobby-tv")
    check("the page is not cached and sends no referrer", r.headers["cache-control"] == "no-store"
          and r.headers["referrer-policy"] == "no-referrer")
    check("only an admin gets the editor", "layout-editor.js" not in r.text and "layout-editor.js" in admin.get("/board/lobby-tv").text)
    check("?edit=1 asks a member for a permission it lacks", "boardData" not in member.get("/board/lobby-tv?edit=1").text)
    eq("live data for the module's widget", tv.get("/board/lobby-tv/_/data").json()["items"], {"cue": {"lit": True}})

with section("pressing a module's button on a board"):
    Fake.calls.clear()
    P = "/board/lobby-tv/_/press/cue"
    eq("signed out, even on a public board: refused", (nobody.post(P).status_code, tv.post(P).status_code, Fake.calls), (403, 403, []))
    eq("a viewer may not fire cues", (viewer.post(P).status_code, Fake.calls), (403, []))
    eq("an operator may: it fires once", (operator.post(P).status_code, Fake.calls), (200, ["walk"]))
    texts = [e["summary"] for e in audit.search(kind="lighting")["entries"]]
    check("it is in the audit log with who pressed it", any("Walk-in" in t for t in texts), texts)
    eq("a board link is not a press", operator.post("/board/lobby-tv/_/press/lnk").status_code, 409)
    eq("an unknown item", operator.post("/board/lobby-tv/_/press/zzz").status_code, 404)

with section("a module that is switched off"):
    asyncio.run(_modules.set_enabled("lighting", False))
    cat = {d["type"] for d in admin.get(B).json()["catalogue"]}
    check("its widget leaves the list", "lighting.cue" not in cat)
    board = boards.find(config.get()["boards"], "lobby-tv")
    check("its widget stays on the board", any(i["id"] == "cue" for i in board["items"]))
    check("the board says it is off", "This widget" in admin.get("/board/lobby-tv").text)
    eq("pressing it is refused", operator.post("/board/lobby-tv/_/press/cue").status_code, 409)
    eq("and it has no data", tv.get("/board/lobby-tv/_/data").json()["items"], {})
    admin.put(f"{B}/lobby-tv", json={"name": "Lobby"})
    check("a save while it is off keeps its widget", any(i["id"] == "cue" for i in boards.find(config.get()["boards"], "lobby-tv")["items"]))

with section("duplicate, export, import, delete"):
    r = admin.post(f"{B}/lobby-tv/duplicate")
    eq("a copy has its own address", r.json()["saved"], "lobby-copy")
    data = admin.get(f"{B}/lobby-tv/export").json()
    check("an export is the layout without an address", data["relay_board"] == 1 and "id" not in data and len(data["items"]) == 6)
    eq("a viewer may export", viewer.get(f"{B}/lobby-tv/export").status_code, 200)
    r = admin.post(f"{B}/import", json=data)
    eq("an import gets an address of its own", r.json()["saved"], "lobby")
    eq("what is not a board file is refused", admin.post(f"{B}/import", json={"name": "x"}).json()["error"], "That is not a Relay board file.")
    eq("a member cannot import", member.post(f"{B}/import", json=data).status_code, 403)
    eq("deleted", [b["id"] for b in admin.delete(f"{B}/lobby").json()["boards"]], ["lobby-tv", "lobby-tv-2", "lobby-copy"])
    eq("its address stops working", member.get("/board/lobby").status_code, 404)
    eq("deleting twice", admin.delete(f"{B}/lobby").status_code, 404)

with section("changes are in the audit log"):
    kinds = {e["action"] for e in audit.search(kind="boards")["entries"]}
    check("make, change and delete are recorded", {"boards.create", "boards.change", "boards.delete"} <= kinds, kinds)

done()
