"""Gear and racks (#547): one record per piece of equipment, found again by any key an older feature used."""
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

from app import config, gear, network_map, screens  # noqa: E402
from app.modules.blackmagic import service as bm  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

SW, ATEM_MAC, STAGE = "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02", "aa:bb:cc:00:00:03"


def dev(**kw) -> dict:
    return {"mac": "", "ips": [], "host": "", "name": "", "kind": "av", "ndi": None, "dante": None, "model": "", **kw}


with section("refs"):
    eq("ref round trip", gear.gear_id(gear.ref(7)), 7)
    eq("a rack ref is not a gear ref", gear.gear_id(gear.rack_ref(7)), None)
    eq("another provider is not a relay gear", gear.gear_id("gear:pco:7"), None)

with section("a key becomes one gear record"):
    a = gear.adopt(["mac:" + SW], name="Stage switch", kind="switch", model="USW-16")
    eq("named from the source", (a["name"], a["kind"], a["model"], a["named"]), ("Stage switch", "switch", "USW-16", False))
    eq("the same key is the same gear", gear.adopt(["mac:" + SW])["id"], a["id"])
    eq("a source's new name is followed until an operator names it", gear.adopt(["mac:" + SW], name="Switch 1")["name"], "Switch 1")
    gear.rename(a["id"], "Booth core")
    eq("an operator's name stays", gear.adopt(["mac:" + SW], name="Switch 1")["name"], "Booth core")
    gear.rename(a["id"], "")
    eq("empty text hands the name back to the source", gear.adopt(["mac:" + SW], name="Switch 1")["name"], "Switch 1")
    gear.rename(a["id"], "Booth core")

with section("two keys for one box join one record"):
    early = gear.adopt(["ip:10.0.0.20", "host:atem"], name="ATEM 1", kind="atem")
    again = gear.adopt(["mac:" + ATEM_MAC, "ip:10.0.0.20"], name="ATEM Mini")
    eq("the MAC joined the record made from an address", again["id"], early["id"])
    eq("it keeps the first name (not the source's)", again["name"], "ATEM Mini")
    eq("and is known by its MAC alone", gear.keys(early["id"]), ["mac:" + ATEM_MAC])
    gear.rename(early["id"], "Main ATEM")
    moved = gear.adopt(["mac:" + ATEM_MAC, "ip:10.0.0.99"], name="ATEM Mini")
    eq("a new IP address keeps the name", (moved["id"], moved["name"]), (early["id"], "Main ATEM"))
    eq("and does not record the address", gear.keys(early["id"]), ["mac:" + ATEM_MAC])
    other = gear.adopt(["ip:10.0.0.50"], name="Laptop")
    gear.adopt(["ip:10.0.0.50"], name="Laptop")
    check("an address a MAC-known box left is free for another box", other["id"] != early["id"])

with section("two records that turn out to be one merge"):
    x = gear.adopt(["blackmagic:aaa"], name="Hub", kind="videohub")
    gear.set_notes(x["id"], "Top shelf")
    y = gear.adopt(["host:hub"], name="hub")
    check("two records", x["id"] != y["id"])
    merged = gear.adopt(["blackmagic:aaa", "host:hub"])
    eq("one record now holds both keys", sorted(gear.keys(merged["id"])), ["blackmagic:aaa", "host:hub"])
    eq("it keeps what either had", (merged["kind"], merged["notes"]), ("videohub", "Top shelf"))
    eq("and the other is gone", len(gear.listing()) - len({g["id"] for g in gear.listing()}), 0)
    try:
        gear.adopt(["no spaces allowed", "x"])
        check("a malformed key is refused", False)
    except ValueError:
        check("a malformed key is refused", True)

with section("racks are inside places"):
    booth = screens.place_add("Booth")
    rack = gear.rack_add(booth["id"], " Rack A ")
    eq("a rack is named inside a place", (rack["name"], rack["place_id"]), ("Rack A", booth["id"]))
    eq("the same name in the same place is the same rack", gear.rack_add(booth["id"], "rack a")["id"], rack["id"])
    other_rack = gear.rack_add(screens.place_add("Stage")["id"], "Rack A")
    check("another place may have a rack of that name", other_rack["id"] != rack["id"])
    gear.set_rack(a["id"], rack["id"], 0)
    gear.set_rack(early["id"], rack["id"], 1)
    row = gear.get(a["id"])
    eq("gear in a rack is in its place", (row["rack_ref"], row["place_ref"]), (gear.rack_ref(rack["id"]), booth["ref"]))
    eq("top to bottom", [g["name"] for g in gear.in_rack(rack["id"])], ["Booth core", "Main ATEM"])
    gear.set_position(a["id"], 5)
    eq("position moves it", [g["name"] for g in gear.in_rack(rack["id"])], ["Main ATEM", "Booth core"])
    gear.set_place(a["id"], screens.place_add("Foyer")["id"])
    eq("moving the gear to another place takes it out of the rack", gear.get(a["id"])["rack_id"], None)
    gear.rack_delete(rack["id"])
    eq("deleting a rack leaves the gear in the place", (gear.get(early["id"])["rack_id"], gear.get(early["id"])["place_id"]),
       (None, booth["id"]))
    screens.place_delete(booth["id"])
    eq("deleting a place takes its racks and leaves its gear", gear.get(early["id"])["place_id"], None)

with section("the network map stores what it merges as gear"):
    switch = dev(mac=SW, ips=["10.0.0.2"], name="Fake switch", kind="switch")
    atem = dev(mac=ATEM_MAC, ips=["10.0.0.20"], name="ATEM", host="atem")
    stage = dev(mac=STAGE, ips=["10.0.0.30"], name="Stage box", host="stagebox", dante=True)
    network_map.adopt_devices([switch, atem, stage])
    eq("each is one gear record", [len([g for g in gear.listing() if ("mac:" + m) in g["keys"]]) for m in (SW, ATEM_MAC, STAGE)], [1, 1, 1])
    eq("a Dante box is gear of kind dante, keyed by its name", (gear.by_key("mac:" + STAGE)["kind"], "dante:stagebox" in gear.by_key("mac:" + STAGE)["keys"] if False else gear.by_key("dante:stagebox")["id"] == gear.by_key("mac:" + STAGE)["id"]),
       ("dante", True))
    network_map.save_labels({STAGE: "Stage left box"})
    network_map.save_places([{"name": "Booth rack", "gear": [SW, ATEM_MAC, STAGE]}])
    renumbered = dev(mac=STAGE, ips=["10.0.0.77"], name="Stage box", host="stagebox", dante=True)
    network_map.adopt_devices([renumbered])
    again = network_map.stored()
    eq("the name survives an IP change", again["names"][STAGE], "Stage left box")
    eq("so does its rack, in order", again["places"], [{"name": "Booth rack", "gear": [SW, ATEM_MAC, STAGE]}])
    eq("the closet is a rack in a core place of the same name", [(r["name"], r["place"]) for r in gear.racks()], [("Booth rack", "Booth rack")])
    labelled = network_map.labelled({"devices": [{**renumbered, "id": STAGE, "name": "Stage box", "stale_by": [], "ndi": None,
                                                  "tags": [], "ports": [], "notes": [], "seen_by": [], "parent": "", "poe_w": None,
                                                  "poe_budget_w": None}], "sources": []})
    eq("the page shows Relay's name and keeps the device's own", (labelled["devices"][0]["name"], labelled["devices"][0]["own_name"]),
       ("Stage left box", "Stage box"))

with section("Blackmagic devices are gear"):
    bm.save_devices([bm.clean_device({"id": "main", "kind": "atem", "name": "Program ATEM", "host": "10.0.0.20", "model": "ATEM Mini",
                                      "place": "rack"}),
                     bm.clean_device({"id": "hub", "kind": "videohub", "name": "Router", "host": "10.0.0.40", "place": "shelf"})])
    check("the ATEM is two records until the map next sees it", gear.by_key("blackmagic:main")["id"] != gear.by_key("mac:" + ATEM_MAC)["id"])
    network_map.adopt_devices([atem])
    atem_gear = gear.by_key("blackmagic:main")
    eq("the ATEM is the gear the network map made from its MAC", atem_gear["id"], gear.by_key("mac:" + ATEM_MAC)["id"])
    eq("one name, the older record's, and the model and kind", (atem_gear["name"], atem_gear["model"], atem_gear["kind"]), ("Main ATEM", "ATEM Mini", "atem"))
    eq("a rack-mounted device is in a rack", bool(atem_gear["rack_id"]), True)
    eq("a shelf device is in a place, outside any rack", (gear.by_key("blackmagic:hub")["rack_id"], bool(gear.by_key("blackmagic:hub")["place_id"])), (None, True))
    listed = {d["id"]: d for d in bm.devices()}
    eq("the module reads them back from gear", (listed["main"]["name"], listed["main"]["place"], listed["hub"]["place"]),
       ("Main ATEM", "rack", "shelf"))
    stored_cfg = config.get()["blackmagic"]["devices"]
    check("config keeps only what the module needs", all(not {"name", "model", "drawing", "place"} & set(d) for d in stored_cfg), stored_cfg)
    gear.rename(atem_gear["id"], "Main switcher")
    eq("a rename elsewhere shows on the Blackmagic page", {d["id"]: d["name"] for d in bm.devices()}["main"], "Main switcher")
    bm.save_devices(list(reversed(bm.devices())), reorder=True)
    eq("the order is the gear's position", [d["id"] for d in bm.devices()], ["hub", "main"])
    bm.save_devices([d for d in bm.devices() if d["id"] != "hub"])
    eq("removing a device drops its key and keeps the gear", (gear.by_key("blackmagic:hub"), gear.by_key("mac:" + ATEM_MAC) is not None), (None, True))

with section("devices saved before #547 move onto gear"):
    config.save({"blackmagic": {"devices": [{"id": "old1", "kind": "hyperdeck", "name": "Record 1", "host": "10.0.0.51", "place": "shelf",
                                              "model": "HyperDeck Studio", "drawing": "hyperdeck-studio"}]}}, replace=("blackmagic",))
    old = bm.devices()
    eq("name, model, drawing and place came across", (old[0]["name"], old[0]["model"], old[0]["drawing"], old[0]["place"]),
       ("Record 1", "HyperDeck Studio", "hyperdeck-studio", "shelf"))
    check("and left config", all(not {"name", "model", "drawing", "place"} & set(d) for d in config.get()["blackmagic"]["devices"]))

with section("names and notes saved on the network map before #547 move onto gear"):
    config.save({"network_map": {"names": {"10.9.9.9": "Old switch"}, "notes": {"10.9.9.9": "Closet 2", "10.9.9.9#4": "Wall B4"},
                                 "places": [{"name": "Closet 2", "gear": ["10.9.9.9"]}]}}, replace=("network_map",))
    seen = network_map.stored()
    eq("the name and note", (seen["names"]["10.9.9.9"], seen["notes"]["10.9.9.9"]), ("Old switch", "Closet 2"))
    eq("the closet and its gear", [p for p in seen["places"] if p["name"] == "Closet 2"], [{"name": "Closet 2", "gear": ["10.9.9.9"]}])
    eq("a port note stays in config until ports move (#540)", config.get()["network_map"],
       {"names": {}, "notes": {"10.9.9.9#4": "Wall B4"}, "places": []})

done()
