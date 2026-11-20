"""Screens and places (#527): one record per display, found again by any id an older feature used."""
from __future__ import annotations

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

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, screens, serving, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

with section("places"):
    foyer = screens.place_add("  Foyer ")
    eq("name tidied", foyer["name"], "Foyer")
    eq("the same name is the same place", screens.place_add("foyer")["id"], foyer["id"])
    stage = screens.place_add("Stage")
    eq("ref round trip", screens.place_id(foyer["ref"]), foyer["id"])
    eq("another provider is not a relay place", screens.place_id("place:pco:3"), None)
    try:
        screens.place_rename(stage["id"], "FOYER")
        check("a taken place name is refused", False)
    except screens.Refused as exc:
        eq("taken name", exc.key, "name_taken")

with section("a key becomes one screen"):
    a = screens.adopt("board:abcd1234", name="Lobby TV", address="10.0.0.5", board="lobby")
    eq("named from the feature's own name", a["name"], "Lobby TV")
    eq("remembers the board it showed", a["last_board"], "lobby")
    again = screens.adopt("board:abcd1234")
    eq("the same key is the same screen", again["id"], a["id"])
    eq("found by key", screens.by_key("board:abcd1234")["id"], a["id"])
    blank = screens.adopt("board:zzzz9999")
    eq("an unnamed key gets a placeholder name", blank["name"], "screen zzzz99")

with section("the same name is the same screen"):
    sig = screens.adopt("signage:lobby", name="lobby tv")
    eq("a signage screen with the board screen's name joins it", sig["id"], a["id"])
    eq("both keys on one screen", sorted(screens.keys(a["id"])), ["board:abcd1234", "signage:lobby"])
    alert = screens.adopt("alert:lobby tv", name="Lobby TV")
    eq("an alert name joins it too", alert["id"], a["id"])
    eq("names by key", screens.names_by_key("signage:"), {"lobby": "Lobby TV"})

with section("renaming merges"):
    other = screens.adopt("board:other0001", name="Foyer left")
    kept = screens.rename(other["id"], "Lobby TV")
    eq("the screen that had the name survives", kept, a["id"])
    check("the merged screen is gone", screens.get(other["id"]) is None)
    check("its key moved", "board:other0001" in screens.keys(a["id"]))
    eq("a free name just renames", screens.rename(blank["id"], "Hall"), blank["id"])

with section("places and what a screen shows"):
    screens.set_place(a["id"], foyer["id"])
    eq("in a place", screens.get(a["id"])["place_ref"], foyer["ref"])
    screens.set_shows(a["id"], normal=("signage", "3"), during=("board", "lobby"), schedule="schedule:relay:svc")
    scr = screens.get(a["id"])
    is_open = lambda ref: ref == "schedule:relay:svc"   # noqa: E731
    eq("the schedule is open", screens.showing(scr, is_open, signage_on=True)["kind"], "board")
    eq("normally signage", screens.showing(scr, lambda ref: False, signage_on=True), {"kind": "signage", "ref": "3", "why": "all the time"})
    eq("signage off falls back to the board", screens.showing(scr, lambda ref: False, signage_on=False)["kind"], "board")
    eq("the board it last showed", screens.showing(scr, lambda ref: False, signage_on=False)["ref"], "lobby")
    try:
        screens.set_shows(a["id"], normal=("tv", "1"))
        check("an unknown kind is refused", False)
    except screens.Refused as exc:
        eq("refused", exc.key, "shows")
    screens.set_shows(a["id"], during=("", ""))
    eq("no second choice, no schedule", screens.get(a["id"])["during_schedule"], "")
    screens.forget_board("lobby")
    eq("a deleted board is forgotten", screens.get(a["id"])["last_board"], "")

with section("panels and the listing"):
    p = screens.adopt("panel:p1", name="Kids door", kind="panel")
    eq("a panel screen", p["kind"], "panel")
    try:
        screens.rename(p["id"], "Door")
        check("a panel is not renamed here", False)
    except screens.Refused as exc:
        eq("renamed on the Panels page", exc.key, "panel_name")
    screens.sync_panel_name(p["id"], "Kids entrance")
    eq("the panel's own name wins", screens.get(p["id"])["name"], "Kids entrance")
    names = [r["name"] for r in screens.listing()]
    check("listed once each", "Lobby TV" in names and "Kids entrance" in names)
    eq("every name once", len(names), len(set(n.lower() for n in names)))
    screens.delete(p["id"])
    check("its keys go with it", db.one("select 1 x from screen_keys where key = 'panel:p1'") is None)
    check("heard from just now", screens.online({"last_seen": db.now()}, __import__("time").time()))
    check("never heard from", not screens.online({"last_seen": ""}, __import__("time").time()))

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"signage": {"enabled": True}}})
modules.attach("signage")
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", base_url="http://relay.local:8001", username="vera")
S = "/api/admin/screens"

with section("the Screens page API"):
    got = admin.post("/api/admin/places", json={"name": "Foyer"}).json()
    place = next(p for p in got["places"] if p["name"] == "Foyer")
    eq("a viewer may look", viewer.get(S).status_code, 200)
    eq("a viewer may not change", viewer.post("/api/admin/places", json={"name": "X"}).status_code, 403)
    tv = screens.adopt("alert:hall tv", name="Hall TV")
    frame = admin.get(S).json()
    row = next(r for r in frame["screens"] if r["id"] == tv["id"])
    eq("a screen with nothing set shows what it was opened on", row["now"]["kind"], "")
    eq("the page lists signage as on", frame["signage_on"], True)
    r = admin.put(f"{S}/{tv['id']}", json={"place_id": place["id"], "normal": {"kind": "signage", "ref": "1"}})
    eq("saved", r.status_code, 200)
    row = next(x for x in r.json()["screens"] if x["id"] == tv["id"])
    eq("it is in the place", row["place"], "Foyer")
    check("a signage screen was made so it can play", any("/signage/" in x for x in row["links"]), row["links"])
    eq("a bad choice is refused with a sentence", admin.put(f"{S}/{tv['id']}", json={"normal": {"kind": "tv", "ref": "1"}}).json()["error"],
       screens.REFUSALS["shows"])
    eq("hold something by hand", admin.put(f"{S}/{tv['id']}/hold", json={"kind": "board", "ref": "lobby"}).status_code, 200)
    eq("a held screen says so", next(x for x in admin.get(S).json()["screens"] if x["id"] == tv["id"])["now"]["why"], "held by hand")
    admin.delete(f"{S}/{tv['id']}/hold")
    eq("back to its schedule", next(x for x in admin.get(S).json()["screens"] if x["id"] == tv["id"])["hold"]["kind"], "")
    eq("flashing a screen nobody has open takes none", admin.post(f"{S}/{tv['id']}/flash").json()["took"], 0)
    eq("a place can be renamed", admin.put(f"/api/admin/places/{place['id']}", json={"name": "Lobby"}).status_code, 200)
    eq("an unknown screen is a 404", admin.put(f"{S}/99999", json={"name": "x"}).status_code, 404)
    eq("a place can be deleted", admin.delete(f"/api/admin/places/{place['id']}").status_code, 200)
    eq("and its screens stay", next(x for x in admin.get(S).json()["screens"] if x["id"] == tv["id"])["place_id"], None)

with section("the signage player follows its screen"):
    slug = next(x for x in admin.get(S).json()["screens"] if x["id"] == tv["id"])["links"][0].rsplit("/", 1)[1]
    serving.publish(8000, 8001)
    serving.publish_listeners({8095: ("/signage",)})
    player = TestClient(app, base_url="http://relay.local:8095")
    screens.set_shows(tv["id"], normal=("board", "lobby"))
    config.save({"boards": [{"id": "lobby", "name": "Lobby", "pages": [{"id": "p1", "name": "One", "items": []}]}]})
    state = player.get(f"/signage/_/state/{slug}").json()
    eq("a screen set to a board sends the player there", state["board"]["path"], f"/board/lobby?screen=screen-{tv['id']}")
    screens.set_shows(tv["id"], normal=("signage", "1"))
    check("a screen set to signage plays", "board" not in player.get(f"/signage/_/state/{slug}").json())

done()
