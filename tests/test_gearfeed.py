"""The one seam for gear, ports and channels (#545): a module offers `gear.<id>`, core records what it lists, and switching
the module off and on adds and removes its gear without losing names, notes or cables."""
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

from app import cables, channels, gear, gearfeed, modules, ports  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

ID = "fakegear"
state = {"seen": True, "boom": False, "inputs": ["Cam 1", "Cam 2"]}


def answer() -> dict | None:
    """What a module's `list()` says: its gear is on gear record `GID`, with connectors and channels."""
    if state["boom"]:
        raise RuntimeError("secret detail")
    if not state["seen"]:
        return None
    return {"devices": [{"ip": "10.0.0.9", "name": "Fake box", "kind": "av"}],
            "ports": [{"gear": GID, "items": [{"key": "in1", "kind": "sdi", "direction": "in", "gear_name": "In 1"},
                                              {"key": "out1", "kind": "sdi", "direction": "out", "gear_name": "Out 1"}]}],
            "channels": [{"gear": GID, "items": [{"key": f"in{n}", "kind": "video", "direction": "source", "number": n,
                                                  "gear_name": name} for n, name in enumerate(state["inputs"], 1)]}]}


def on() -> None:
    modules.provide(f"gear.{ID}", {"list": answer}, owner=ID)         # what a module does in attach()


def off() -> None:
    modules.withdraw_all(ID)                                          # what the registry does when it is switched off


GID = gear.adopt([f"{ID}:box"], name="Fake box", kind="av", heard=False)["id"]

with section("nothing is recorded while the module is off"):
    eq("nothing to ask", gearfeed.collect(ID), None)
    eq("changed records nothing", gearfeed.changed(ID), None)
    eq("no channels", channels.of_provider(ID), [])

with section("a module offers its gear and core records it"):
    on()
    eq("the devices are read for the map", [d["name"] for d in gearfeed.devices(ID)], ["Fake box"])
    refs = gearfeed.changed(ID)
    eq("every channel comes back by key", sorted(refs), ["in1", "in2"])
    eq("the connectors are core ports on its gear", sorted(p["key"] for p in ports.of_gear(GID)), ["in1", "out1"])
    eq("the signals are core channels", sorted(e["key"] for c in channels.of_provider(ID) for e in c["ends"]), ["in1", "in2"])

with section("a name, a note and a cable survive the module being switched off and on"):
    pin = ports.by_key(GID, "in1")
    pout = ports.by_key(GID, "out1")
    ports.set_note(pin["id"], "Wall plate")
    ch = channels.by_ref(refs["in1"])
    channels.rename(ch["id"], "Pulpit")
    cable = cables.add(pin["id"], pout["id"])
    gear.rename(GID, "Booth box")
    off()
    eq("off: nothing is offered", (gearfeed.collect(ID), gearfeed.devices(ID)), (None, []))
    eq("off: its ports are still there", len(ports.of_gear(GID)), 2)
    eq("off: its channels are still there", sorted(c["shown"] for c in channels.listing("video")), ["Cam 2", "Pulpit"])
    on()
    again = gearfeed.changed(ID)
    eq("on again: the same channels", again, refs)
    eq("the port note is kept", ports.by_key(GID, "in1")["note"], "Wall plate")
    eq("the channel name is kept", channels.by_ref(refs["in1"])["shown"], "Pulpit")
    eq("the gear name is kept", gear.get(GID)["name"], "Booth box")
    check("the cable is kept", any(c["id"] == cable["id"] for c in cables.listing()))

with section("what the module stops listing turns missing, not deleted"):
    state["inputs"] = ["Cam 1"]
    gearfeed.changed(ID)
    gone = {e["key"]: e["missing"] for c in channels.of_provider(ID) for e in c["ends"]}
    eq("in2 is missing and in1 is not", gone, {"in1": False, "in2": True})

with section("a module that cannot see its gear says nothing, so nothing turns missing"):
    state["seen"] = False
    eq("None means nothing was recorded", gearfeed.changed(ID), None)
    check("its channels are not missing", not any(e["missing"] for c in channels.of_provider(ID) for e in c["ends"]
                                                  if e["key"] == "in1"))
    state["seen"] = True

with section("a module's own failure does not reach core or the page"):
    state["boom"] = True
    eq("changed swallows it", gearfeed.changed(ID), None)
    eq("collect swallows it", gearfeed.collect(ID), None)
    try:
        gearfeed.devices(ID)
        raised = False
    except RuntimeError:
        raised = True                      # the map catches it and shows a fixed sentence
    check("the map's read lets it through to say 'could not be read'", raised)
    state["boom"] = False

off()
done()
