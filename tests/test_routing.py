"""Core routes (#544): a destination channel takes a source channel, the module that owns the destination applies it and
reports what the gear does, a difference is shown, and a preset (salvo) takes a set of routes."""
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

from app import channels, config, modules, routing  # noqa: E402
from app.main import app  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
admin = _db.client(app, base_url="http://relay.local:8001")


class Gear:
    """A fake router: real code, no sockets. What it does is `state`: {destination key: [(source key, gain)]}."""

    def __init__(self, exclusive: bool, gain: bool) -> None:
        self.exclusive, self.gain, self.state, self.refuse = exclusive, gain, {}, ""

    def api(self, provider: str) -> dict:
        def apply(end, wanted):
            if self.refuse:
                return self.refuse
            picked = [(e["key"], w["gain_db"]) for w in wanted for e in w["ends"] if e["provider"] == provider]
            self.state[end["key"]] = picked
            return ""

        def read(end):
            if end["key"] not in self.state:
                return None
            return [{"provider": provider, "key": k, "gain_db": g} for k, g in self.state[end["key"]]]

        return {"exclusive": self.exclusive, "gain": self.gain, "apply": apply, "read": read}


video, audio = Gear(True, False), Gear(False, True)
modules.provide("routes.vid", video.api("vid"), owner="test")
modules.provide("routes.aud", audio.api("aud"), owner="test")
def report() -> None:
    channels.sync("vid", [{"key": "in1", "kind": "video", "direction": "source", "gear_name": "Camera 2"},
                          {"key": "in2", "kind": "video", "direction": "source", "gear_name": "Camera 3"},
                          {"key": "out1", "kind": "video", "direction": "destination", "gear_name": "Lobby TV"},
                          {"key": "out2", "kind": "video", "direction": "destination", "gear_name": "Green room"}])
    channels.sync("aud", [{"key": "l1", "kind": "audio", "direction": "source", "gear_name": "Vocal"},
                          {"key": "l2", "kind": "audio", "direction": "source", "gear_name": "Pulpit"},
                          {"key": "o1", "kind": "audio", "direction": "destination", "gear_name": "Main L"}])


report()
R = {c["shown"]: c["ref"] for c in channels.listing()}


def refused(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except routing.Refused as exc:
        return exc.key
    return ""


with section("a destination takes a source and the module applies it"):
    got = routing.set(R["Lobby TV"], R["Camera 2"])
    eq("no problems", got["problems"], [])
    eq("the gear was told", video.state["out1"], [("in1", None)])
    eq("the gear agrees with the ask", got["status"]["state"], "same")
    eq("what feeds it", [s["name"] for s in routing.feeds(R["Lobby TV"])], ["Camera 2"])
    eq("where the source goes", [d["name"] for d in routing.feeding(R["Camera 2"])], ["Lobby TV"])

with section("a video destination takes one source"):
    routing.set(R["Lobby TV"], R["Camera 3"])
    eq("the new one replaced the old", [s["name"] for s in routing.feeds(R["Lobby TV"])], ["Camera 3"])
    eq("the gear follows", video.state["out1"], [("in2", None)])

with section("an audio destination mixes and keeps a gain"):
    routing.set(R["Main L"], R["Vocal"], -6)
    routing.set(R["Main L"], R["Pulpit"], -3.5)
    eq("both feed it with their gains", sorted((s["name"], s["gain_db"]) for s in routing.feeds(R["Main L"])),
       [("Pulpit", -3.5), ("Vocal", -6.0)])
    routing.clear(R["Main L"], R["Vocal"])
    eq("one source cleared", [s["name"] for s in routing.feeds(R["Main L"])], ["Pulpit"])
    eq("a video route stores no gain", routing.set(R["Green room"], R["Camera 2"], -3)["status"]["wanted"][0]["gain_db"], None)

with section("the gear is the truth"):
    video.state["out1"] = [("in1", None)]            # someone switched it on the gear
    v = routing.status(R["Lobby TV"])
    eq("a difference is shown", v["state"], "differs")
    eq("with what the gear does", [s["name"] for s in v["actual"]], ["Camera 2"])
    eq("and what was asked", [s["name"] for s in v["wanted"]], ["Camera 3"])
    eq("what feeds it is the gear's word", [s["name"] for s in routing.feeds(R["Lobby TV"])], ["Camera 2"])
    del video.state["out1"]
    eq("a gear that cannot say shows what was asked", routing.status(R["Lobby TV"])["state"], "wanted")
    eq("a restart applies it again", routing.reapply("vid"), [])
    eq("the gear is told again", video.state["out1"], [("in2", None)])

with section("a refusal is kept as asked and said"):
    video.refuse = "The router said no."
    got = routing.set(R["Lobby TV"], R["Camera 2"])
    eq("the sentence comes back", got["problems"], ["The router said no."])
    eq("the ask is kept", [s["name"] for s in got["status"]["wanted"]], ["Camera 2"])
    video.refuse = ""

with section("what does not fit is refused"):
    eq("itself", refused(routing.set, R["Lobby TV"], R["Lobby TV"]), "same")
    eq("audio into video", refused(routing.set, R["Lobby TV"], R["Vocal"]), "kind")
    eq("a source as a destination", refused(routing.set, R["Camera 2"], R["Camera 3"]), "no_destination")
    eq("a destination as a source", refused(routing.set, R["Green room"], R["Lobby TV"]), "no_source")
    eq("no such channel", refused(routing.set, "channel:relay:999", R["Camera 2"]), "no_channel")

with section("presets"):
    routing.set(R["Lobby TV"], R["Camera 2"])
    s = routing.save_salvo("Sunday", [R["Lobby TV"], R["Green room"]], scope="vid")
    eq("saved with both routes", sorted((x["destination_name"], x["source_name"]) for x in s["routes"]),
       [("Green room", "Camera 2"), ("Lobby TV", "Camera 2")])
    eq("a name is its scope's own", refused(routing.save_salvo, "sunday", [R["Lobby TV"]], "vid"), "name_taken")
    eq("listed by scope", [x["name"] for x in routing.salvos("vid")], ["Sunday"])
    routing.set(R["Lobby TV"], R["Camera 3"])
    eq("taking it puts the routes back", routing.take_salvo(s["id"]), [])
    eq("the gear follows", video.state["out1"], [("in1", None)])
    routing.clear(R["Green room"])
    s2 = routing.save_salvo("Dark", [R["Green room"]], scope="vid")
    eq("nothing is saved as nothing", s2["routes"][0]["source"], "")
    routing.set(R["Green room"], R["Camera 3"])
    routing.take_salvo(s2["id"])
    eq("taking it clears the output", routing.feeds(R["Green room"]), [])
    routing.delete_salvo(s["id"])
    eq("deleted", [x["name"] for x in routing.salvos("vid")], ["Dark"])

with section("a merged channel keeps its routes"):
    a = channels.sync("vid", [{"key": "in1", "kind": "video", "direction": "source", "gear_name": "Camera 2"}])
    routing.set(R["Lobby TV"], R["Camera 2"])
    chan = channels.by_ref(R["Camera 2"])
    channels.sync("other", [{"key": "x", "kind": "video", "direction": "source", "gear_name": "Cam two"}])
    other = channels.of_provider("other")[0]
    channels.merge(chan["id"], other["id"])
    eq("the route still shows its source", [s["name"] for s in routing.feeds(R["Lobby TV"])], ["Camera 2"])

with section("the admin API"):
    admin.get("/api/admin/routes")                                      # the app's start-up resets what modules offered
    report()                                                            # and marks what was reported missing
    modules.provide("routes.vid", video.api("vid"), owner="test")
    modules.provide("routes.aud", audio.api("aud"), owner="test")
    r = admin.post("/api/admin/routes", json={"destination": R["Lobby TV"], "source": R["Camera 3"]})
    eq("a route is made", (r.status_code, r.json()["problems"], r.json()["status"]["state"]), (200, [], "same"))
    r = admin.get("/api/admin/routes", params={"channel": R["Lobby TV"]})
    eq("what feeds it", [x["name"] for x in r.json()["feeds"]], ["Camera 3"])
    check("and the list holds the route", "Lobby TV" in [x["name"] for x in r.json()["routes"]])
    eq("where a source goes", [d["name"] for d in admin.get("/api/admin/routes", params={"channel": R["Camera 3"]}).json()["feeding"]],
       ["Lobby TV"])
    eq("audio into video is refused with a sentence", admin.post("/api/admin/routes", json={"destination": R["Lobby TV"], "source": R["Vocal"]}).json(),
       {"error": routing.REFUSALS["kind"]})
    eq("a channel that is not there is a 404", admin.post("/api/admin/routes", json={"destination": "channel:relay:999", "source": R["Camera 3"]}).status_code, 404)
    r = admin.post("/api/admin/routes/salvos", json={"name": "Api", "destinations": [R["Lobby TV"]], "scope": "vid"})
    eq("a preset is saved", (r.status_code, r.json()["name"]), (200, "Api"))
    sid = r.json()["id"]
    admin.post("/api/admin/routes/clear", json={"destination": R["Lobby TV"]})
    eq("a route is cleared", routing.feeds(R["Lobby TV"]), [])
    eq("a preset is taken", (admin.post(f"/api/admin/routes/salvos/{sid}/take").json(), [x["name"] for x in routing.feeds(R["Lobby TV"])]),
       ({"problems": []}, ["Camera 3"]))
    eq("a preset is deleted", (admin.delete(f"/api/admin/routes/salvos/{sid}").status_code, admin.delete(f"/api/admin/routes/salvos/{sid}").status_code), (200, 404))

with section("a board button makes the same route"):
    operator = _db.client(app, role="operator", username="op1", base_url="http://relay.local:8001")
    bid = admin.post("/api/admin/boards", json={"name": "Lobby"}).json()["saved"]
    cat = {d["type"]: d for d in admin.get("/api/admin/boards").json()["catalogue"]}
    check("the route button is offered", "route" in cat)
    names = lambda i: sorted(o[1] for o in cat["route"]["fields"][i]["options"])  # noqa: E731
    eq("it offers what takes a signal, then what gives one", (names(0), names(1)),
       (["Green room", "Lobby TV", "Main L"], ["Camera 2", "Camera 3", "Pulpit", "Vocal"]))
    admin.put(f"/api/admin/boards/{bid}", json={"items": [{"id": "go", "type": "route", "x": 0, "y": 0, "w": 3, "h": 1,
                                                            "props": {"destination": R["Green room"], "source": R["Camera 3"]}}]})
    eq("someone who may not manage routes is refused", operator.post(f"/board/{bid}/_/press/go").status_code, 403)
    eq("an admin presses it", admin.post(f"/board/{bid}/_/press/go").json(), {"ok": True})
    eq("the destination takes the source", [x["name"] for x in routing.feeds(R["Green room"])], ["Camera 3"])
    eq("and the gear was told", video.state["out2"], [("in2", None)])

with section("a module that is off applies nothing and says so"):
    modules.withdraw_all("test")
    got = routing.set(R["Lobby TV"], R["Camera 3"])
    eq("saved", [s["name"] for s in got["status"]["wanted"]], ["Camera 3"])
    eq("with the reason", got["problems"], [routing.NO_PROVIDER])

done()
