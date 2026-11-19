"""Core channels (#542): a channel is one record made of the ends modules report; Relay suggests joins and an operator
confirms them; a tally camera, a rundown channel and the Network page all show the same name."""
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

from app import audiobus, channels, config, gear, tally  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.blackmagic.service import Service as BlackmagicService  # noqa: E402
from app.modules.ndi.service import Service as NdiService  # noqa: E402
from app.modules.obs.service import Machine as ObsMachine  # noqa: E402
from app.modules.planning_center import rundown  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
admin = _db.client(app, base_url="http://relay.local:8001")


def refused(fn, *args, **kw) -> str:
    try:
        fn(*args, **kw)
    except channels.Refused as exc:
        return exc.key
    return ""


def video(key: str, name: str, **kw) -> dict:
    return {"key": key, "kind": "video", "direction": kw.pop("direction", "source"), "gear_name": name, **kw}


ATEM = gear.adopt(["blackmagic:main"], name="ATEM", kind="atem")["id"]

with section("refs"):
    eq("round trip", channels.channel_id(channels.ref(4)), 4)
    eq("a port ref is not a channel ref", channels.channel_id("port:relay:4"), None)
    eq("another provider is not a relay channel", channels.channel_id("channel:pco:4"), None)

with section("a module reports its channels; each gets a channel of its own"):
    got = channels.sync("ndi", [video("src:BOOTH (Camera 2)", "Camera 2"), video("src:BOOTH (Camera 3)", "Camera 3")])
    eq("a ref per key", sorted(got), ["src:BOOTH (Camera 2)", "src:BOOTH (Camera 3)"])
    cam2 = channels.by_ref(got["src:BOOTH (Camera 2)"])
    eq("shown under the end's own name", (cam2["shown"], cam2["kind"], cam2["providers"], cam2["missing"]),
       ("Camera 2", "video", ["ndi"], False))
    again = channels.sync("ndi", [video("src:BOOTH (Camera 2)", "Camera 2 (renamed)"), video("src:BOOTH (Camera 3)", "Camera 3")])
    eq("reporting again keeps the same channel", again["src:BOOTH (Camera 2)"], cam2["ref"])
    eq("and takes the gear's new name", channels.by_ref(cam2["ref"])["shown"], "Camera 2 (renamed)")
    eq("a bad provider id is ignored", channels.sync("Not Valid", [video("a", "x")]), {})
    eq("a bad kind is ignored", (channels.sync("ndi", [{"key": "zz", "kind": "radio"}]),
                                 "zz" in {e["key"] for c in channels.listing() for e in c["ends"]}), ({}, False))

with section("a channel no longer reported is missing, not dropped"):
    channels.rename(cam2["id"], "Camera Two")
    channels.sync("ndi", [video("src:BOOTH (Camera 3)", "Camera 3")])
    gone = channels.by_ref(cam2["ref"])
    eq("missing, with the operator's name kept", (gone["missing"], gone["shown"]), (True, "Camera Two"))
    channels.sync("ndi", [video("src:BOOTH (Camera 2)", "Camera 2"), video("src:BOOTH (Camera 3)", "Camera 3")])
    eq("and back when it is reported again", channels.by_ref(cam2["ref"])["missing"], False)
    eq("a present channel cannot be forgotten", refused(channels.forget, cam2["id"]), "present")

with section("one provider, several machines: `within` limits what turns missing"):
    channels.sync("obs", [video("m1:Wide", "Wide")], within="m1:")
    channels.sync("obs", [video("m2:Wide", "Wide")], within="m2:")
    eq("the first machine's scene is still there", [e["missing"] for c in channels.of_provider("obs") for e in c["ends"]
                                                    if e["key"] == "m1:Wide"], [False])

with section("gear: channels on one device are scoped to it"):
    channels.sync("blackmagic", [video("main:in2", "Camera 2", number=2), video("main:in3", "Cam 3", number=3)], ATEM)
    channels.sync("blackmagic", [video("main:in2", "Camera 2", number=2)], ATEM)
    eq("one the device no longer has is missing", {e["key"]: e["missing"] for c in channels.of_gear(ATEM) for e in c["ends"]},
       {"main:in2": False, "main:in3": True})
    eq("a gear that is not there reports nothing", channels.sync("blackmagic", [video("x", "x")], 99999), {})

with section("suggestions: the same name or the same number on the same gear, never joined on their own"):
    pairs = channels.suggestions()
    eq("Camera 2 from NDI and from the ATEM, by the name the ATEM and NDI both report",
       [(p["a"]["shown"], p["b"]["shown"], p["why"]) for p in pairs if "Camera 2" in (p["a"]["shown"], p["b"]["shown"])],
       [("Camera Two", "Camera 2", "name")])
    channels.sync("ndi", [video("src:G (input 4)", "Input 4")])
    channels.sync("blackmagic", [video("main:in2", "Camera 2", number=2), video("main:in4", "Input 4", number=4)], ATEM)
    check("a name a device gives everything is not a reason", not any({p["a"]["shown"], p["b"]["shown"]} == {"Input 4"}
                                                                  for p in channels.suggestions()))
    eq("no channel was joined by itself", len([c for c in channels.listing() if len(c["ends"]) > 1]), 0)

with section("an operator joins, splits and declines"):
    ndi_cam = channels.by_ref(channels.sync("ndi", [video("src:BOOTH (Camera 2)", "Camera 2"),
                                                    video("src:BOOTH (Camera 3)", "Camera 3")])["src:BOOTH (Camera 2)"])
    bm_cam = next(c for c in channels.of_gear(ATEM) if c["ends"][0]["key"] == "main:in2")
    check("the two are suggested", any({p["a"]["id"], p["b"]["id"]} == {ndi_cam["id"], bm_cam["id"]} for p in channels.suggestions()))
    joined = channels.merge(ndi_cam["id"], bm_cam["id"])
    eq("one channel with both ends", (len(joined["ends"]), joined["providers"]), (2, ["blackmagic", "ndi"]))
    eq("the old ref follows to it", channels.by_ref(bm_cam["ref"])["id"], joined["id"])
    eq("a merged channel is listed once", len([c for c in channels.listing() if c["ref"] in (ndi_cam["ref"], bm_cam["ref"])]), 1)
    eq("a channel is not joined to itself", refused(channels.merge, joined["id"], bm_cam["id"]), "same")
    audio = channels.by_ref(channels.sync("audiobus", [{"key": "lane1", "kind": "audio", "number": 1}])["lane1"])
    eq("a different kind cannot be joined", refused(channels.merge, joined["id"], audio["id"]), "kind")
    eq("the ends report on after a join", channels.sync("ndi", [video("src:BOOTH (Camera 2)", "Camera 2")])["src:BOOTH (Camera 2)"],
       joined["ref"])
    back = channels.split(joined["ends"][1]["id"])
    eq("a split takes one end out", (len(channels.get(joined["id"])["ends"]), len(back["ends"])), (1, 1))
    eq("the only end cannot be split", refused(channels.split, back["ends"][0]["id"]), "only_end")
    channels.decline(joined["id"], back["id"])
    check("a declined pair is not suggested again", not any({p["a"]["id"], p["b"]["id"]} == {joined["id"], back["id"]}
                                                            for p in channels.suggestions()))
    channels.rename(joined["id"], "")
    channels.rename(back["id"], "Typed on the other")
    channels.merge(joined["id"], back["id"])
    eq("the other channel's typed name is used when the kept one has none", channels.get(joined["id"])["name"], "Typed on the other")

with section("a name an operator types shows everywhere"):
    chan = channels.by_ref(channels.sync("ndi", [video("src:BOOTH (Camera 2)", "Camera 2")])["src:BOOTH (Camera 2)"])
    channels.rename(chan["id"], "  Camera   Two ")
    eq("one name", channels.by_ref(chan["ref"])["shown"], "Camera Two")
    eq("and an empty one gives the gear's name back", (channels.rename(chan["id"], "") or channels.by_ref(chan["ref"])["shown"]),
       "Camera 2")
    channels.rename(chan["id"], "Camera Two")
    eq("a ref that was merged away has the new name too", channels.names()[chan["ref"]], "Camera Two")
    eq("describe: a found channel", channels.describe(chan["ref"], "old")["name"], "Camera Two")
    eq("describe: a ref that is gone keeps the typed name", channels.describe("channel:relay:9999", "old"),
       {"ref": "", "name": "old", "missing": False, "found": False})
    eq("a channel that is not there", refused(channels.rename, 9999, "x"), "no_channel")

with section("a tally camera points at its video channel and shows its name"):
    tally.tally.reset()
    tally.tally.publish("atem_main", [{"n": 2, "name": "Camera 2", "program": True, "preview": False, "channel": chan["ref"]},
                                      {"n": 3, "name": "Cam 3", "channel": "not a ref"}])
    cams = {c["n"]: c for c in tally.tally.cameras()}
    eq("named from the channel", (cams[2]["name"], cams[2]["channel"]), ("Camera Two", chan["ref"]))
    eq("a camera with no channel keeps its name", (cams[3]["name"], "channel" in cams[3]), ("Cam 3", False))
    v = tally.tally.version()
    channels.rename(chan["id"], "Cam Two")
    check("a rename reaches the tally screens", tally.tally.version() != v)
    eq("with the new name", {c["n"]: c["name"] for c in tally.tally.cameras()}[2], "Cam Two")
    tally.tally.reset()

with section("Blackmagic reports a switcher's inputs and a Videohub's inputs and outputs"):
    found = BlackmagicService._video_channels({"id": "sw", "kind": "atem", "inputs": [{"id": 1, "name": "Cam 1"}]})
    eq("a switcher input is a source", [(c["key"], c["direction"], c["number"]) for c in found], [("sw:in1", "source", 1)])
    hub = {"id": "hub", "kind": "videohub", "videohub": {"inputs": [{"n": 0, "label": "Stage"}],
                                                          "outputs": [{"n": 0, "label": "Wall"}]}}
    eq("a Videohub's are sources and destinations", [(c["key"], c["direction"], c["number"]) for c in
                                                      BlackmagicService._video_channels(hub)],
       [("hub:vin0", "source", 1), ("hub:vout0", "destination", 1)])

with section("NDI reports the sources it sees online, and OBS its scenes"):
    ndi = NdiService()
    ndi._seen = {"STUDIO (PTZ)": {"name": "STUDIO (PTZ)", "machine": "STUDIO", "source": "PTZ", "missed": 0},
                 "STUDIO (Old)": {"name": "STUDIO (Old)", "machine": "STUDIO", "source": "Old", "missed": 0}}
    ndi.report_channels()
    ndi._seen["STUDIO (Old)"]["missed"] = 5
    ndi.report_channels()
    ends = {e["key"]: e["missing"] for c in channels.of_provider("ndi") for e in c["ends"] if e["key"].startswith("src:STUDIO")}
    eq("an online source is reported; one that went offline is missing", ends, {"src:STUDIO (PTZ)": False, "src:STUDIO (Old)": True})
    obs = ObsMachine({"id": "m1", "name": "Stream PC", "host": "127.0.0.1", "port": 4455}, "")
    obs.scenes = ["Wide", "Pulpit"]
    obs._report_channels()
    other = ObsMachine({"id": "m2", "name": "Backup PC", "host": "127.0.0.2", "port": 4455}, "")
    other.scenes = ["Wide"]
    other._report_channels()
    eq("each machine's scenes are its own", sorted((e["key"], e["missing"]) for c in channels.of_provider("obs") for e in c["ends"]
                                                   if e["key"].startswith("m") and e["key"][1] in "12" and e["key"][2] == ":"),
       [("m1:Pulpit", False), ("m1:Wide", False), ("m2:Wide", False)])

with section("the audio bus reports its lanes and outputs"):
    audiobus.report_channels(audiobus.normalize({"lanes": [{"lane": 3, "name": "Pastor", "channel": 0}],
                                                 "outputs": [{"id": "foh", "name": "FOH"}]}))
    got = {(e["key"], e["direction"], e["number"]) for c in channels.of_provider("audiobus") for e in c["ends"]
           if not e["missing"]}
    eq("patched lanes and outputs", got, {("lane3", "source", 3), ("out:foh", "destination", None)})

with section("a rundown channel is tied to a core channel, and a typed name is matched once"):
    lane = next(c for c in channels.of_provider("audiobus") if c["ends"][0]["key"] == "lane3")
    rs = rundown.clean_rundown({"channels": [{"id": "c1", "name": "pastor", "number": "9", "positions": []},
                                             {"id": "c2", "name": "Choir 1", "number": "", "positions": []},
                                             {"id": "c3", "name": "x", "channel_ref": "junk", "positions": []}],
                                "mics": [{"team": "Band", "position": "Vocals", "mics": ["Pastor", "HH4"]}]})
    eq("a ref that is not one is dropped", rs["channels"][2]["channel_ref"], "")
    check("nothing is linked until the directory refresh", rs["channels"][0]["channel_ref"] == "")
    rundown.refresh_saved(rs)
    eq("a typed name that matches one channel adopts it", (rs["channels"][0]["channel_ref"], rs["channels"][0]["name"],
                                                          rs["channels"][0]["number"]), (lane["ref"], "Pastor", "3"))
    eq("a name that matches none stays name only", rs["channels"][1]["channel_ref"], "")
    eq("a mic label that matches adopts the channel", rs["mics"][0]["mic_refs"], {"Pastor": lane["ref"]})
    channels.rename(lane["id"], "Lead")
    rundown.refresh_saved(rs)
    eq("a rename follows to the board", rs["channels"][0]["name"], "Lead")
    audiobus.report_channels(audiobus.normalize({"lanes": [], "outputs": []}))
    rundown.refresh_saved(rs)
    eq("a channel the gear no longer has keeps its name and its ref", (rs["channels"][0]["name"], rs["channels"][0]["channel_ref"]),
       ("Lead", lane["ref"]))
    check("and is flagged missing for the page", channels.by_ref(lane["ref"])["missing"])
    saved = rundown.clean_rundown(rs)
    eq("saving keeps the ref", saved["channels"][0]["channel_ref"], lane["ref"])

with section("two same-named channels are not guessed"):
    channels.sync("audiobus", [{"key": "lane7", "kind": "audio", "number": 7, "gear_name": "Dup"},
                               {"key": "lane8", "kind": "audio", "number": 8, "gear_name": "dup"}])
    rs = rundown.clean_rundown({"channels": [{"id": "c1", "name": "DUP", "positions": []}]})
    rundown.refresh_saved(rs)
    eq("more than one match leaves it name only", rs["channels"][0]["channel_ref"], "")

with section("a rename goes to the gear only when its module can (#543)"):
    from app import modules as _mods  # noqa: E402
    from app import ports as _ports  # noqa: E402

    class Gear:  # a stand-in provider with real behaviour: it keeps names like a router would
        def __init__(self):
            self.names = {"r1": "Router in A"}

        def can_rename(self, key):
            return key in self.names

        def rename(self, key, name):
            if name == "refuse":
                raise ValueError("no")
            self.names[key] = name

    box = Gear()
    _mods.provide("gear.testgear", {"can_rename": box.can_rename, "rename": box.rename}, owner="testgear")
    channels.sync("testgear", [{"key": "r1", "kind": "video", "gear_name": "Router in A"},
                               {"key": "fixed", "kind": "video", "gear_name": "Router fixed"}])
    ch = next(c for c in channels.listing("video") if c["gear_name"] == "Router in A")
    fx = next(c for c in channels.listing("video") if c["gear_name"] == "Router fixed")
    eq("which can be renamed on the gear", (ch["can_device"], fx["can_device"]), (True, False))
    eq("a gear that cannot is refused", refused(channels.rename_on_device, fx["id"], "X"), "no_device")
    eq("an empty name is refused", refused(channels.rename_on_device, ch["id"], " "), "name")
    eq("a gear that refuses leaves Relay alone", (refused(channels.rename_on_device, ch["id"], "refuse"),
                                                  channels.get(ch["id"])["shown"]), ("device", "Router in A"))
    channels.rename(ch["id"], "Relay name")
    channels.rename_on_device(ch["id"], "Cam A")
    got = channels.get(ch["id"])
    eq("the gear took it and Relay shows it", (box.names["r1"], got["shown"], got["name"]), ("Cam A", "Cam A", ""))
    channels.rename(ch["id"], "Relay name")
    channels.sync("testgear", [{"key": "r1", "kind": "video", "gear_name": "Renamed at the router"},
                               {"key": "fixed", "kind": "video", "gear_name": "Router fixed"}])
    got = channels.get(ch["id"])
    eq("a rename made on the gear shows beside a Relay-only name, which stays",
       (got["shown"], got["gear_name"]), ("Relay name", "Renamed at the router"))
    _mods.withdraw("gear.testgear")
    eq("a module that is off cannot rename", channels.get(ch["id"])["can_device"], False)
    g = gear.adopt(["test:p1"], name="Box")
    p = _ports.adopt(g["id"], "1", gear_name="Port 1")
    res = admin.post(f"/api/admin/network/ports/relay/{p['id']}/rename", json={"name": "Stage in"})
    body = res.json()
    eq("a port is renamed in Relay only and the gear's name is kept", (res.status_code, body["device_updated"],
       body["port"]["shown"], body["port"]["gear_name"]), (200, False, "Stage in", "Port 1"))
    eq("a port that is not there", admin.post("/api/admin/network/ports/relay/9999/rename", json={"name": "x"}).status_code, 404)

with section("the Network page's API"):
    res = admin.get("/api/admin/network/channels")
    body = res.json()
    eq("lists the channels and the suggestions", (res.status_code, len(body["channels"]) >= 4, isinstance(body["suggestions"], list)),
       (200, True, True))
    pick = next(c for c in body["channels"] if c["shown"] == "Cam Two")
    eq("rename", (admin.post(f"/api/admin/network/channels/{pick['id']}/rename", json={"name": "Wide"}).status_code,
                  channels.by_ref(pick["ref"])["shown"]), (200, "Wide"))
    eq("a name that is too long", admin.post(f"/api/admin/network/channels/{pick['id']}/rename", json={"name": "x" * 61}).status_code, 400)
    eq("a note", admin.post(f"/api/admin/network/channels/{pick['id']}/note", json={"text": "Stage left"}).status_code, 200)
    eq("a channel that is not there", admin.post("/api/admin/network/channels/9999/rename", json={"name": "x"}).status_code, 404)
    other = next(c for c in body["channels"] if c["kind"] == "audio")
    res = admin.post("/api/admin/network/channels/merge", json={"keep": pick["ref"], "other": other["ref"]})
    eq("a join across kinds is a conflict with a fixed sentence", (res.status_code, res.json()["error"]),
       (409, channels.REFUSALS["kind"]))
    eq("a ref that is not a ref", admin.post("/api/admin/network/channels/merge", json={"keep": "x", "other": other["ref"]}).status_code, 400)
    res = admin.post("/api/admin/network/channels/decline", json={"a": pick["ref"], "b": other["ref"]})
    eq("decline", res.status_code, 200)
    eq("split with no end", admin.post("/api/admin/network/channels/split", json={"end": "x"}).status_code, 400)
    eq("forget a present channel", admin.delete(f"/api/admin/network/channels/{pick['id']}").status_code, 409)
    gone = next(c for c in channels.listing() if c["missing"])
    eq("forget a missing one", (admin.delete(f"/api/admin/network/channels/{gone['id']}").status_code,
                                channels.by_ref(gone["ref"])), (200, None))

done()
