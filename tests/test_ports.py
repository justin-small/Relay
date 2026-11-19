"""Core ports (#540): every physical connector on a piece of gear, stored and refreshed from whatever reports it."""
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

from app import gear, ports  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402


def refused(fn, *args, **kw) -> str:
    try:
        fn(*args, **kw)
    except ports.Refused as exc:
        return exc.key
    return ""


SWITCH = gear.adopt(["mac:aa:bb:cc:00:00:40"], name="Booth switch", kind="switch")["id"]
ATEM = gear.adopt(["blackmagic:main"], name="ATEM", kind="atem")["id"]

with section("refs"):
    eq("round trip", ports.port_id(ports.ref(12)), 12)
    eq("a gear ref is not a port ref", ports.port_id(gear.ref(12)), None)
    eq("another provider is not a relay port", ports.port_id("port:pco:3"), None)
    eq("a TCP number is not a port", ports.port_id(9910), None)

with section("a source reports its ports, and they are stored"):
    rows = ports.sync(SWITCH, [{"key": "1", "kind": "sfp", "gear_name": "Uplink", "link_up": True, "speed_mbps": 10000},
                               {"key": "2", "kind": "eth", "link_up": False, "poe_w": 4.26, "errors": 3},
                               {"key": "3", "kind": "eth"},
                               {"key": "2", "kind": "eth"}, {"key": ""}, "junk"])
    eq("each port once, in the order given", [r["key"] for r in rows], ["1", "2", "3"])
    eq("kind, name and refs", (rows[0]["kind"], rows[0]["shown"], rows[0]["gear_ref"]), ("sfp", "Uplink", gear.ref(SWITCH)))
    eq("the link: up, down, and unknown when the source does not say",
       [r["link_up"] for r in rows], [True, False, None])
    eq("network numbers as reported", (rows[1]["poe_w"], rows[1]["errors"], rows[0]["speed_mbps"]), (4.3, 3, 10000.0))
    eq("a kind Relay does not know is left blank, a direction defaults to both",
       (ports.sync(ATEM, [{"key": "in1", "kind": "sdi", "direction": "in"}, {"key": "x", "kind": "laser", "direction": "?"}])[1]["kind"],
        ports.by_key(ATEM, "x")["direction"], ports.by_key(ATEM, "in1")["direction"]), ("", "both", "in"))

with section("a port the source stops reporting is kept, missing, with its note"):
    ports.set_note(ports.by_key(SWITCH, "3")["id"], "  Wall   plate B3 ")
    rows = {r["key"]: r for r in ports.sync(SWITCH, [{"key": "1", "kind": "sfp", "link_up": True}, {"key": "2", "kind": "eth"}])}
    eq("missing, link unknown, note kept", (rows["3"]["missing"], rows["3"]["link_up"], rows["3"]["note"]), (True, None, "Wall plate B3"))
    ports.sync(SWITCH, [{"key": "1"}, {"key": "2"}, {"key": "3"}])
    eq("one that is back is not missing", ports.by_key(SWITCH, "3")["missing"], False)
    rows = {r["key"]: r for r in ports.sync(SWITCH, [], keep=["1"])}
    eq("`keep` holds a port the caller still has", (rows["1"]["missing"], rows["2"]["missing"]), (False, True))
    eq("notes of a piece of gear", ports.notes(SWITCH), {"3": "Wall plate B3"})

with section("a note or a name before the source lists the port"):
    p = ports.adopt(SWITCH, "24", heard=False)
    eq("the row is made, with nothing filled in", (p["kind"], p["gear_name"], p["last_seen"]), ("", "", ""))
    ports.rename(p["id"], "Projector")
    eq("a Relay-only name shows over the gear's", ports.get(p["id"])["shown"], "Projector")
    ports.rename(p["id"], "")
    eq("empty text shows the gear's own name again", ports.get(p["id"])["shown"], "24")
    again = ports.adopt(SWITCH, "24", kind="eth", gear_name="Port 24")
    eq("heard later: filled in, same row", (again["id"], again["kind"], again["gear_name"]), (p["id"], "eth", "Port 24"))
    eq("a key that is not one is refused", refused(ports.adopt, SWITCH, "bad\nkey"), "key")
    eq("a note on gear that is not there is refused", refused(ports.adopt, 99999, "1"), "no_gear")

with section("forgetting"):
    gone = ports.by_key(SWITCH, "2")
    present = ports.adopt(SWITCH, "1", kind="sfp")
    eq("a port still reported is not forgotten", refused(ports.forget, present["id"]), "present")
    ports.forget(gone["id"])
    eq("a missing one is", ports.by_key(SWITCH, "2"), None)
    eq("an unknown one is refused", refused(ports.forget, 99999), "no_port")
    gear.delete(ATEM)
    eq("deleting the gear removes its ports", ports.of_gear(ATEM), [])
    eq("several gear in one read", sorted(ports.of_gears([SWITCH, ATEM])), sorted([ATEM, SWITCH]))

done()
