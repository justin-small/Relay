"""Boards (#156): the store, who may open, change and press what on which port, widget settings checked against their
definitions, a module's widget (lighting) through the registry, export and import; pages and what decides the page
shown (one, a rotation, a schedule, following ProPresenter, a page chosen by hand), places, templates, and the screens
showing a board. A fake lighting program.
Run: python tests/test_boards.py
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
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
from app import audit, boards, config, screens  # noqa: E402
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
    eq("a module widget is kept with plain settings only", got[1]["pages"][0]["items"][0]["props"], {"cue": "a", "n": None})
    eq("an item with a bad type is dropped", len(got[1]["pages"][0]["items"]), 1)
    eq("a board saved before pages has its widgets on page 1", [(p["id"], p["name"]) for p in got[1]["pages"]],
       [("p1", "Page 1")])
    eq("and shows one page, with no place", (got[1]["rotation"]["mode"], got[1]["place"]), ("one", ""))

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
    items = {i["id"]: i for i in r.json()["board"]["pages"][0]["items"]}
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
    check("the screen never carries the editor", "layout-editor.js" not in r.text and "layout-editor.js" not in admin.get("/board/lobby-tv").text)
    r = admin.get("/board/lobby-tv?edit=1", follow_redirects=False)
    eq("an old ?edit=1 link takes an admin to the Boards page", (r.status_code, r.headers.get("location")), (303, "/admin#boards"))
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
    check("its widget stays on the board", any(i["id"] == "cue" for i in boards.all_items(board)))
    check("the board says it is off", "This widget" in admin.get("/board/lobby-tv").text)
    eq("pressing it is refused", operator.post("/board/lobby-tv/_/press/cue").status_code, 409)
    eq("and it has no data", tv.get("/board/lobby-tv/_/data").json()["items"], {})
    admin.put(f"{B}/lobby-tv", json={"name": "Lobby"})
    check("a save while it is off keeps its widget", any(i["id"] == "cue" for i in boards.all_items(boards.find(config.get()["boards"], "lobby-tv"))))

with section("duplicate, export, import, delete"):
    r = admin.post(f"{B}/lobby-tv/duplicate")
    eq("a copy has its own address", r.json()["saved"], "lobby-copy")
    data = admin.get(f"{B}/lobby-tv/export").json()
    check("an export is the layout without an address", data["relay_board"] == 2 and "id" not in data
          and len(data["pages"][0]["items"]) == 6 and "screen_names" not in data)
    eq("a viewer may export", viewer.get(f"{B}/lobby-tv/export").status_code, 200)
    r = admin.post(f"{B}/import", json=data)
    eq("an import gets an address of its own", r.json()["saved"], "lobby")
    old = {"relay_board": 1, "name": "Old", "ratio": "16:9", "items": [{"type": "clock", "x": 0, "y": 0, "w": 2, "h": 2}]}
    r = admin.post(f"{B}/import", json=old)
    eq("a file saved before pages still imports, onto page 1", len(boards.find(r.json()["boards"], r.json()["saved"])["pages"][0]["items"]), 1)
    admin.delete(f"{B}/{r.json()['saved']}")
    eq("what is not a board file is refused", admin.post(f"{B}/import", json={"name": "x"}).json()["error"], "That is not a Relay board file.")
    eq("a member cannot import", member.post(f"{B}/import", json=data).status_code, 403)
    eq("deleted", [b["id"] for b in admin.delete(f"{B}/lobby").json()["boards"]], ["lobby-tv", "lobby-tv-2", "lobby-copy"])
    eq("its address stops working", member.get("/board/lobby").status_code, 404)
    eq("deleting twice", admin.delete(f"{B}/lobby").status_code, 404)

with section("pages on a board"):
    clock = {"id": "c1", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 2, "props": {}}
    r = admin.put(f"{B}/lobby-tv-2", json={"pages": [
        {"id": "p1", "name": "Welcome", "items": [clock]},
        {"name": "  Captions  ", "items": [dict(clock), {"id": "cue2", "type": "lighting.cue", "x": 2, "y": 0, "w": 2, "h": 1}]},
        {"id": "BAD ID", "name": "", "items": []}] + [{"name": f"Extra {n}"} for n in range(20)]})
    b = r.json()["board"]
    eq("pages are kept up to the limit", len(b["pages"]), boards.MAX_PAGES)
    eq("a page without a good id gets one; a blank name gets a number",
       [(pg["id"], pg["name"]) for pg in b["pages"][:3]], [("p1", "Welcome"), ("p2", "Captions"), ("p3", "Page 3")])
    ids = [it["id"] for it in boards.all_items(b)]
    eq("widget ids are unique across the whole board", len(ids), len(set(ids)))
    admin.put(f"{B}/lobby-tv-2", json={"pages": b["pages"][:3]})
    eq("a button on page 2 is found (its module is off here, so it is refused as off, not as missing)",
       operator.post("/board/lobby-tv-2/_/press/cue2").status_code, 409)
    page = member.get("/board/lobby-tv-2").text
    check("the screen gets every page", all(f'"{pid}"' in page for pid in ("p1", "p2", "p3")))

with section("what decides the page shown"):
    b = admin.put(f"{B}/lobby-tv-2", json={"rotation": {
        "mode": "schedule", "every": 2, "pages": ["p2", "nope", "p1"],
        "schedule": [{"days": "sun", "from": "9:00", "to": "10:30", "show": "p2"},
                     {"days": "weekdays", "from": "22:00", "to": "02:00", "show": "rotate"},
                     {"days": "someday", "from": "99:99", "to": "x", "show": "gone"}],
        "other": "p3", "follow": {"rules": [{"match": "group", "value": " Sermon ", "page": "p2"}, {"match": "x", "value": ""}],
                                  "none": "gone"}}}).json()["board"]
    rot = b["rotation"]
    eq("a rotation is at least 5 seconds and only through pages that exist", (rot["every"], rot["pages"]), (5, ["p2", "p1"]))
    eq("times are cleaned and bad ones fall back", [(x["days"], x["from"], x["to"], x["show"]) for x in rot["schedule"]],
       [("sun", "09:00", "10:30", "p2"), ("weekdays", "22:00", "02:00", "rotate"), ("all", "09:00", "10:00", "p1")])
    eq("a rule needs a value; an unknown page in 'nothing matches' keeps the page", (len(rot["follow"]["rules"]), rot["follow"]["none"]),
       (1, "keep"))
    sun930, sun11 = datetime(2026, 11, 22, 9, 30), datetime(2026, 11, 22, 11, 0)
    got = boards.current(b, sun930)
    eq("inside a row: its page, and why", (got["page"], got["rotate"], got["why"]),
       ("p2", None, "Schedule: Captions until 10:30, then Page 3"))
    eq("outside every row (and past the one that covers all day at 9-10): the other page", boards.current(b, sun11)["page"], "p3")
    eq("a row past midnight belongs to the day it started", (boards.current(b, datetime(2026, 11, 24, 1, 0))["rotate"] is not None,
       boards.current(b, datetime(2026, 11, 23, 1, 0))["page"]), (True, "p3"))
    rotating = boards.current(b, datetime(2026, 11, 23, 23, 0))
    eq("a rotating row turns through its pages", rotating["rotate"], {"pages": ["p2", "p1"], "every": 5})
    # A row may name a schedule from the Schedules page (#526) instead of its own days and times.
    ref = "schedule:relay:svc1"
    named = {**b, "rotation": {**rot, "mode": "schedule", "schedule": [{"days": "all", "from": "09:00", "to": "10:00", "schedule": ref, "show": "p2"}]}}
    open_now = lambda r, at: r == ref and at.hour == 11
    got = boards.current(named, sun11, None, None, open_now)
    eq("a row naming a schedule shows its page while that schedule is open", (got["page"], got["key"]), ("p2", "s0"))
    eq("and the other page outside it", boards.current(named, sun930, None, None, open_now)["page"], "p3")
    eq("with no way to ask, a named row never covers", boards.current(named, sun11)["page"], "p3")
    eq("a ref that is not one is dropped when saved", boards.clean_rotation(
        {"mode": "schedule", "schedule": [{"schedule": "bogus", "show": "p1"}, {"schedule": ref, "show": "p1"}]}, ["p1"])["schedule"][0]["schedule"], "")
    b["rotation"]["mode"] = "follow"
    eq("following: a rule matches whatever the case and spaces", boards.current(b, sun11, {"group": "sermon"})["page"], "p2")
    eq("nothing matches: the page stays", boards.current(b, sun11, {"group": "Worship"}, last="p3")["page"], "p3")
    eq("ProPresenter not connected: the page stays", boards.current(b, sun11, None, last="p2")["key"], "follow-off")
    b["rotation"]["mode"] = "one"
    eq("one page: always the first", boards.current(b, sun930)["page"], "p1")
    eq("a board that is not public tells the viewer port nothing", tv.get("/board/lobby-tv-2/_/data").status_code, 404)
    eq("(signed in) the page it shows now", member.get("/board/lobby-tv-2/_/data").json()["page"],
       boards.current(boards.find(config.get()["boards"], "lobby-tv-2"), datetime.now(ZoneInfo(config.timezone())).replace(tzinfo=None))["page"])

with section("a page chosen by hand"):
    admin.put(f"{B}/lobby-tv-2", json={"rotation": {"mode": "one"}})
    PG = f"{B}/lobby-tv-2/page"
    eq("a member cannot choose", member.post(PG, json={"page": "p2"}).status_code, 403)
    eq("a page that is not there", admin.post(PG, json={"page": "p9"}).json()["error"], "That page is not on this board.")
    live = admin.post(PG, json={"page": "p2"}).json()["live"]["lobby-tv-2"]
    eq("chosen: every screen shows it", (live["page"], live["override"], member.get("/board/lobby-tv-2/_/data").json()["page"]),
       ("p2", True, "p2"))
    admin.put(f"{B}/lobby-tv-2", json={"rotation": {"mode": "rotate", "every": 20}})
    live = admin.get(f"{B}/live").json()["live"]["lobby-tv-2"]
    eq("it holds until what decides the page changes", (live["override"], live["rotate"]["every"]), (False, 20))
    admin.post(PG, json={"page": "p3"})
    live = admin.post(PG, json={"page": ""}).json()["live"]["lobby-tv-2"]
    eq("back to automatic", (live["override"], live["rotate"] is not None), (False, True))

with section("screens showing a board"):
    sid, other = "scr-AAAA1111", "scr-BBBB2222"
    tv.get(f"/board/lobby-tv/_/data?screen={sid}")
    nobody.get(f"/board/lobby-tv/_/data?screen={other}")
    tv.get("/board/lobby-tv/_/data?screen=<script>")
    rows = admin.get(f"{B}/live").json()["live"]["lobby-tv"]["screens"]
    eq("each screen that asked is listed once, a bad id is not", sorted(r["id"] for r in rows), [sid, other])
    rev = tv.get("/board/lobby-tv/_/data").json()["rev"]
    eq("a member cannot name one", member.put(f"{B}/lobby-tv/screens/{sid}", json={"name": "TV"}).status_code, 403)
    eq("not a screen id", admin.put(f"{B}/lobby-tv/screens/x", json={"name": "TV"}).json()["error"], "That is not a screen.")
    rows = admin.put(f"{B}/lobby-tv/screens/{sid}", json={"name": "  Foyer   TV left "}).json()["live"]["lobby-tv"]["screens"]
    eq("named, and named ones come first", (rows[0]["id"], rows[0]["name"]), (sid, "Foyer TV left"))
    eq("a name is not a change to what the screens draw", tv.get("/board/lobby-tv/_/data").json()["rev"], rev)
    check("an export leaves screen names out", "screen_names" not in admin.get(f"{B}/lobby-tv/export").json())
    n = tv.get("/board/lobby-tv/_/data").json()["reload"]
    eq("a member cannot reload the screens", member.post(f"{B}/lobby-tv/reload").status_code, 403)
    admin.post(f"{B}/lobby-tv/reload")
    eq("Reload all screens: the count the screens watch moves", tv.get("/board/lobby-tv/_/data").json()["reload"], n + 1)
    admin.put(f"{B}/lobby-tv", json={"name": "Lobby again"})
    check("a saved change moves the revision, so the screens load it", tv.get("/board/lobby-tv/_/data").json()["rev"] != rev)
    eq("a name cannot be cleared: a screen always has one", admin.put(f"{B}/lobby-tv/screens/{sid}", json={"name": ""}).status_code, 400)
    eq("the name is the core screen's", screens.by_key(f"board:{sid}")["name"], "Foyer TV left")

with section("places and templates"):
    frame = admin.get(B).json()
    eq("the templates offered", [t["id"] for t in frame["templates"]], ["lobby", "stage", "captions", "buttons", "sound", "blank"])
    r = admin.post(B, json={"name": "Stage", "template": "stage", "place": "  Stage  "})
    b = boards.find(r.json()["boards"], r.json()["saved"])
    eq("a template lays out page 1 from real widgets", [it["type"] for it in b["pages"][0]["items"]], ["clock", "countdown", "captions"])
    eq("a place is kept as plain text", b["place"], "Stage")
    eq("a template's shape is used unless another is asked for",
       boards.find(admin.post(B, json={"name": "Pad", "template": "buttons"}).json()["boards"], "pad")["ratio"], "4:3")
    eq("an unknown template is a blank board", boards.find(admin.post(B, json={"name": "Odd", "template": "nope"}).json()["boards"],
                                                           "odd")["pages"][0]["items"], [])
    eq("a place can be changed", admin.put(f"{B}/stage", json={"place": "Booth"}).json()["board"]["place"], "Booth")

with section("following ProPresenter: what the module offers"):
    from app.modules.propresenter.propresenter import follower
    pp = _modules.get("propresenter")
    follower.up = True
    follower.presentation = {"uuid": "u1", "name": "Sunday Service", "path": "", "library": "", "groups": [("Welcome", 1), ("Sermon", 2)],
                             "cues": [{"group": "Welcome", "color": "", "label": ""}, {"group": "Sermon", "color": "", "label": "POINT 1"},
                                      {"group": "Sermon", "color": "", "label": "POINT 2"}]}
    follower.slide_index = 1
    got = pp.board_follow()
    eq("what shows now", got["now"], {"presentation": "Sunday Service", "group": "Sermon", "label": "POINT 1"})
    eq("names to pick from", (got["suggest"]["group"], got["suggest"]["label"], got["suggest"]["presentation"]),
       (["Welcome", "Sermon"], ["POINT 1", "POINT 2"], ["Sunday Service"]))
    follower.up, follower.presentation, follower.slide_index = False, None, None

with section("changes are in the audit log"):
    kinds = {e["action"] for e in audit.search(kind="boards")["entries"]}
    check("make, change and delete are recorded", {"boards.create", "boards.change", "boards.delete"} <= kinds, kinds)

done()
