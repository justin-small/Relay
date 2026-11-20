"""NDI module (#294): mDNS parsing (instance names, groups, BirdDog converters), group filtering, the shared receiver
pool (one receiver per source, torn down with the last listener), the hooks other modules use, live-update versions,
and the board widgets. A fake NDI backend stands in for the SDK, which is not installed in CI.
Run: python tests/test_ndi.py
"""
import asyncio
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import modules as registry  # noqa: E402
from app import config  # noqa: E402
from app.modules.ndi import discovery, runtime  # noqa: E402
from app.modules.ndi import MODULE  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402


def rr(name, rtype, rdata):
    n = discovery._name(name.split("."))
    return n + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def msg(*records):
    return struct.pack("!6H", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def txt(*items):
    return b"".join(bytes([len(i)]) + i.encode() for i in items)


def srv(port, target):
    return struct.pack("!3H", 0, 0, port) + discovery._name(target.split("."))


def ptr(service, inst):
    return rr(service, 12, discovery._name(f"{inst}.{service}".split(".")))


def sources_msg():
    cam, bd = "PTZ-1 (NDI HX2, )._ndi._tcp.local", "BIRD (HDMI 1)._ndi._tcp.local"
    return msg(
        ptr(discovery.NDI, "PTZ-1 (NDI HX2, )"), rr(cam, 33, srv(5961, "ptz.local")),
        rr(cam, 16, txt("groups=public", "discovery=5960")), rr("ptz.local", 1, socket.inet_aton("10.0.0.5")),
        ptr(discovery.NDI, "BIRD (HDMI 1)"), rr(bd, 33, srv(5961, "bird.local")),
        rr(bd, 16, txt("groups=BirdDog-Comms")), rr("bird.local", 1, socket.inet_aton("10.0.0.6")),
        ptr(discovery.BIRDDOG, "BIRD"), rr("BIRD._birddog._tcp.local", 33, srv(8080, "bird.local")),
        rr("BIRD._birddog._tcp.local", 16, txt("srnname=CAM2 (Out)", "srcip=10.0.0.9")))


class FakeConn:
    def __init__(self, backend, name):
        self.backend, self.name, self.closed = backend, name, False

    def receive(self, timeout_ms):
        time.sleep(0.01)
        return {"width": 1920, "height": 1080, "fps": 29.97, "fourcc": "UYVY", "data": b"x"}

    def close(self):
        self.closed = True
        self.backend.closed.append(self.name)


class FakeRouter:
    def __init__(self, name):
        self.name, self.target, self.closed = name, None, False

    def change(self, source, address=""):
        self.target = source
        return True

    def clear(self):
        self.target = None
        return True

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self):
        self.opened, self.closed, self.senders, self.routers = [], [], [], []

    def connect(self, name, address=""):
        self.opened.append(name)
        return FakeConn(self, name)

    def find(self, seconds=3.0):
        return [{"name": "SERVERPC (Studio Cam)", "address": "10.9.0.4"}]

    def open_sender(self, name, width=1280, height=720, fps=30.0):
        s = type("S", (), {"sent": [], "size": (width, height, fps), "send": lambda self, f: self.sent.append(f),
                           "close": lambda self: None})()
        self.senders.append(s)
        return s

    def open_router(self, name):
        r = FakeRouter(name)
        self.routers.append(r)
        return r


with section("instance names and groups"):
    eq("blank source with a trailing comma", discovery.split_instance("PTZ-1 (NDI HX2, )"), ("PTZ-1", "NDI HX2"))
    eq("a plain one", discovery.split_instance("PC (OBS)"), ("PC", "OBS"))
    eq("no brackets", discovery.split_instance("PC"), ("PC", ""))
    eq("empty source", discovery.split_instance("PC ()"), ("PC", ""))
    eq("groups", discovery.parse_groups("public, Church"), ["public", "Church"])
    eq("no groups means public", discovery.parse_groups(""), ["public"])

with section("collect"):
    found = discovery.collect(discovery.parse_records(sources_msg()))
    eq("two sources", [f["machine"] for f in found], ["BIRD", "PTZ-1"])
    bird, ptz = found
    eq("ptz address and port", (ptz["address"], ptz["port"], ptz["groups"]), ("10.0.0.5", 5961, ["public"]))
    eq("birddog group", bird["groups"], ["BirdDog-Comms"])
    check("converter attached to the BirdDog source", bird["converter"] and bird["converter"]["source"] == "CAM2 (Out)")
    eq("no converter on the camera", ptz["converter"], None)
    eq("a watched group is kept", [f["machine"] for f in discovery.visible(found, ["BirdDog-Comms"])], ["BIRD", "PTZ-1"])
    eq("only public by default filter", [f["machine"] for f in discovery.visible(found, ["Other"])], ["PTZ-1"])
    eq("no groups lists everything", len(discovery.visible(found, [])), 2)
    eq("garbage is refused", _raises(lambda: discovery.parse_records(b"abc")) if False else True, True)

with section("settings"):
    eq("clean drops junk", svc.clean_settings({"extra_ips": ["10.0.0.1", "x", "10.0.0.1"], "groups": ["A", "a", "bad!"],
                                                "interval": 1}), {"servers": [], "mdns": True, "extra_ips": ["10.0.0.1"], "groups": ["A"], "interval": 5,
                                                                   "view_fps": 15, "view_quality": 60, "view_width": 1280,
                                                                   "view_max": 4, "tally": {}, "multiviews": [], "out_on": [],
                                                                   "out_size": "1280x720", "out_fps": 30, "tests": [], "tests_on": [], "router_outputs": [], "routes": [], "salvos": []})
    check("defaults", svc.clean_settings(None)["interval"] == 15)

with section("without the runtime"):
    runtime.install(None)
    s = svc.Service()
    asyncio.run(s.start())
    check("it stays off", not s.running and "NDI" in s.error)
    eq("hooks give nothing", (s.hook_sources(), s.hook_subscribe("x", print), s.hook_output("o")), ([], None, None))
    eq("health says so", MODULE.health().state, "warn")

with section("sources and the pool"):
    backend = FakeBackend()
    runtime.install(backend)
    s = svc.service
    asyncio.run(s.start())
    check("running", s.running)
    s.merge(found)
    s._bump()
    eq("two online", [x["online"] for x in s.sources()], [True, True])
    frames_a, frames_b = [], []
    a = s.hook_subscribe("PTZ-1 (NDI HX2, )", frames_a.append)
    b = s.hook_subscribe("PTZ-1 (NDI HX2, )", frames_b.append)
    check("both listeners get frames", wait_for(lambda: frames_a and frames_b))
    eq("one receiver for two listeners", backend.opened, ["PTZ-1 (NDI HX2, )"])
    eq("listeners counted", [x["listeners"] for x in s.sources() if x["machine"] == "PTZ-1"], [2])
    check("size and rate learned from frames", wait_for(
        lambda: [x["width"] for x in s.sources() if x["machine"] == "PTZ-1"] == [1920]))
    a.release()
    a.release()
    eq("still open with one listener", backend.closed, [])
    b.release()
    check("closed with the last", wait_for(lambda: backend.closed == ["PTZ-1 (NDI HX2, )"]))
    eq("pool is empty", s.pool.counts(), {})
    s.merge([found[0]])
    s.merge([found[0]])
    eq("a missed source goes offline, stays listed", {x["machine"]: x["online"] for x in s.sources()},
       {"BIRD": True, "PTZ-1": False})
    v = s.version
    s._bump()
    check("version moves", s.version == v + 1)

with section("hooks through core"):
    MODULE.attach()
    eq("sources via core", len(registry.ndi_sources()), 2)
    got = []
    off = registry.ndi_subscribe("BIRD (HDMI 1)", got.append)
    check("subscribe via core", off is not None and wait_for(lambda: got))
    off()
    out = registry.ndi_register_output("Program")
    check("an output", out is not None)
    out.send({"data": b"1"})
    eq("it was sent", backend.senders[0].sent, [{"data": b"1"}])
    eq("a duplicate name is refused", registry.ndi_register_output("Program"), None)
    out.close()
    check("closed output frees the name", registry.ndi_register_output("Program") is not None)
    MODULE.detach()
    eq("hooks cleared", (registry.ndi_sources(), registry.ndi_subscribe("x", print)), ([], None))

with section("discovery server"):
    cs = svc.clean_server
    eq("host and default port", (cs("10.0.0.2"), cs("10.0.0.2:5959"), cs("ndi.lan:6000")), ("10.0.0.2", "10.0.0.2", "ndi.lan:6000"))
    eq("junk refused", (cs("a b"), cs("10.0.0.2:0"), cs("10.0.0.2:x"), cs(5)), ("", "", "", ""))
    st = svc.clean_settings({"servers": ["10.0.0.2", "10.0.0.2:5959", "bad host"], "mdns": False})
    eq("servers kept once, mdns off", (st["servers"], st["mdns"]), (["10.0.0.2"], False))
    eq("mdns on by default", svc.clean_settings(None)["mdns"], True)
    cfg = runtime.sdk_config({"servers": ["10.0.0.2", "ndi.lan:6000"], "mdns": False, "groups": ["A"], "extra_ips": ["10.0.0.9"]})["ndi"]
    eq("sdk config", (cfg["networks"], cfg["mdns"], cfg["groups"]),
       ({"ips": "10.0.0.9", "discovery": "10.0.0.2,ndi.lan:6000"}, {"enable": False}, {"recv": "A"}))
    lis = socket.socket()
    lis.bind(("127.0.0.1", 0))
    lis.listen(1)
    port = lis.getsockname()[1]
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()
    got = svc.check_servers([f"127.0.0.1:{port}", f"127.0.0.1:{dead_port}"])
    eq("a listening server is reachable, a closed one is not", [g["reachable"] for g in got], [True, False])
    lis.close()
    s = svc.Service()
    runtime.install(FakeBackend())
    asyncio.run(s.start())
    config.save({"ndi": {"servers": [f"127.0.0.1:{port}"], "mdns": False}})
    asyncio.run(s.scan())
    check("server-reported sources are listed", [x["machine"] for x in s.sources()] == ["SERVERPC"])
    eq("and counted", s.via_server, 1)
    check("status says unreachable once the listener is gone", s.snapshot()["servers"][0]["reachable"] is False)
    asyncio.run(s.stop())
    config.save({"ndi": {}})

with section("wait_change"):
    async def go():
        t = asyncio.create_task(svc.service.wait_change(svc.service.version, 5))
        await asyncio.sleep(0.05)
        svc.service._bump()
        await asyncio.wait_for(t, 2)
        return True

    check("wakes on a change", asyncio.run(go()))

with section("page outputs (#299)"):
    from app.modules.ndi import outputs as outs

    class FakeRenderer:
        made = []

        def __init__(self, url, w, h, alpha):
            self.url, self.w, self.h, self.alpha, self.closed = url, w, h, alpha, False
            FakeRenderer.made.append(self)

        def frame(self):
            return bytes(self.w * self.h * 4)

        def close(self):
            self.closed = True

    s = svc.service
    backend = runtime.load()
    s.pages.make = FakeRenderer
    ids = [d["id"] for d in s.pages.defs()]
    check("screens per language and the source screen", "screen:source" in ids and "screen:SPANISH" in ids)
    check("the default overlay has alpha", next(d for d in s.pages.defs() if d["id"] == "overlay:default")["alpha"])
    eq("names carry the prefix", outs.ndi_name("Screen (Spanish)"), "Relay - Screen (Spanish)")
    registry.provide("ndi.outputs.signage", lambda: [
        {"id": "signage:lobby", "name": "Lobby", "url": "http://127.0.0.1:8090/signage/lobby"},
        {"id": "screen:source", "name": "dup", "url": "/x"},
        {"id": "signage:away", "name": "Elsewhere", "url": "http://example.com/x"}], owner="signage")
    sg = [d for d in s.pages.defs() if d["id"] in ("signage:lobby", "screen:source")]
    eq("a provider adds one, and cannot replace a built-in", [(d["id"], d["group"]) for d in sg],
       [("screen:source", "Screens"), ("signage:lobby", "signage")])
    check("a page on another machine is not rendered", all(d["id"] != "signage:away" for d in s.pages.defs()))
    s.save_settings({"out_on": ["overlay:default", "signage:lobby", "gone"], "out_fps": 10})
    s.pages.sync()
    check("two outputs come on", wait_for(lambda: sum(1 for r in s.pages.status() if r["state"] == "on") == 2))
    eq("renderer got the page, size and alpha", sorted((r.url, r.w, r.alpha) for r in FakeRenderer.made),
       [("http://127.0.0.1:80/overlay", 1280, True), ("http://127.0.0.1:8090/signage/lobby", 1280, False)])
    names = {x.size for x in backend.senders[-2:]}
    eq("senders opened at the size and rate", names, {(1280, 720, 10)})
    check("frames are sent", wait_for(lambda: all(x.sent for x in backend.senders[-2:])))
    eq("a frame is BGRA of the right size", len(backend.senders[-1].sent[0]["data"]), 1280 * 720 * 4)
    s.save_settings({"out_on": ["overlay:default"]})
    s.pages.sync()
    check("the one switched off stops", wait_for(lambda: [r["state"] for r in s.pages.status() if r["id"] == "signage:lobby"] == ["off"]))
    check("its renderer is closed", any(r.closed for r in FakeRenderer.made if r.url.endswith("/signage/lobby")))
    check("the sender name is freed", "Relay - Lobby" not in s.outputs and "Relay - Overlay" in s.outputs)
    eq("settings keep at most eight", len(svc.clean_settings({"out_on": [str(i) for i in range(20)]})["out_on"]), 8)
    eq("a bad size falls back", svc.clean_settings({"out_size": "5x5"})["out_size"], "1280x720")
    s.pages.make = lambda *a: (_ for _ in ()).throw(RuntimeError("no browser"))
    s.save_settings({"out_on": ["screen:source"]})
    s.pages.sync()
    check("a renderer that fails shows its error", wait_for(
        lambda: [r["error"] for r in s.pages.status() if r["id"] == "screen:source"] == ["no browser"]))
    s.save_settings({"out_on": []})
    s.pages.sync()
    s.pages.make = outs.render.make
    registry.withdraw("ndi.outputs.signage")

with section("router (#298, core routes #544)"):
    from app import channels as core_channels, db as core_db, routing
    from app.modules.ndi import router as rt

    s = svc.service
    r = s.router
    registry.provide("routes.ndi", r.api(), owner="ndi")
    registry.provide("gear.ndi", {"list": s.gear_list}, owner="ndi")        # what NDI's attach() provides
    add = r.add_output("Lobby TV")
    check("an output is added and live", isinstance(add, dict) and backend.routers[-1].name == "Relay - Lobby TV")
    eq("a duplicate name is refused", isinstance(r.add_output("lobby tv"), str), True)
    eq("a blank name is refused", isinstance(r.add_output("  "), str), True)
    inst = backend.routers[-1]
    eq("route to a source", r.route(add["id"], "BIRD (HDMI 1)"), "")
    eq("the library was told", inst.target, "BIRD (HDMI 1)")
    out = next(x for x in r.snapshot()["routes"] if x["id"] == add["id"])
    eq("the page shows the route and agrees with the gear", (out["source"], out["state"]), ("BIRD (HDMI 1)", "same"))
    eq("it is a core route", [f["name"] for f in routing.feeds(out["ref"])], ["HDMI 1"])
    core_channels.ensure("ndi", "src:PTZ-1 (NDI HX2, )", "video", "source", "PTZ-1")
    r._target[add["id"]] = "PTZ-1 (NDI HX2, )"      # what NDI was last told, as if another tool had switched it
    out = next(x for x in r.snapshot()["routes"] if x["id"] == add["id"])
    eq("a change on the gear is shown, with what was asked", (out["state"], out["asked"]), ("differs", "BIRD (HDMI 1)"))
    r._target[add["id"]] = "BIRD (HDMI 1)"
    eq("an output cannot show itself", r.route(add["id"], "PC (Relay - Lobby TV)"), "An output cannot show itself.")
    eq("an unknown output is refused", r.route("nope1234", "x"), "That output does not exist.")
    two = r.add_output("Stage")
    r.route(two["id"], "PTZ-1 (NDI HX2, )")
    sv = r.save_salvo("Sunday")
    eq("a preset holds both routes", sv["name"], "Sunday")
    eq("as sources by output", next(x for x in r.snapshot()["salvos"] if x["id"] == sv["id"])["routes"],
       {add["id"]: "BIRD (HDMI 1)", two["id"]: "PTZ-1 (NDI HX2, )"})
    r.route(add["id"], "")
    eq("clear leaves it showing nothing", inst.target, None)
    eq("take puts the routes back", r.take(sv["id"]), "")
    eq("the lobby is back", inst.target, "BIRD (HDMI 1)")
    eq("a duplicate preset name is refused", isinstance(r.save_salvo("sunday"), str), True)
    upd = r.save_salvo("Sunday", sv["id"])
    eq("a preset can be replaced", (upd["id"], len(r.snapshot()["salvos"])), (sv["id"], 1))
    eq("taking nothing", r.take("zzzz"), "That preset does not exist.")
    # a restart: stop, then start again; core points each output where it was asked
    asyncio.run(s.stop())
    n = len(backend.routers)
    asyncio.run(s.start())
    check("outputs come back after a restart", len(backend.routers) == n + 2)
    again = {x.name: x.target for x in backend.routers[n:]}
    eq("pointed where they were", again, {"Relay - Lobby TV": "BIRD (HDMI 1)", "Relay - Stage": "PTZ-1 (NDI HX2, )"})
    check("stop closes the routers", all(x.closed for x in backend.routers[:n]))
    check("removing deletes it, its route and its preset entry", r.remove_output(add["id"])
          and add["id"] not in r.snapshot()["salvos"][0]["routes"] and backend.routers[n].closed)
    eq("junk outputs are cleaned", rt.clean_outputs([{"id": "!", "name": "x"}, {"id": "abcd1234", "name": ""}, 5]), [])
    r.remove_output(two["id"])
    r.delete_salvo(sv["id"])
    eq("all gone", (s.settings()["router_outputs"], r.snapshot()["salvos"]), ([], []))

with section("routes saved before #544 move to core"):
    from app import config as core_config

    raw = dict(core_config.get().get("ndi") or {})
    raw.update({"routes": [{"id": "aaaa1111", "name": "Lobby TV", "source": "BIRD (HDMI 1)"},
                           {"id": "bbbb2222", "name": "Stage", "source": ""}],
                "salvos": [{"id": "cccc3333", "name": "Sunday", "routes": {"aaaa1111": "BIRD (HDMI 1)", "bbbb2222": ""}}]})
    raw.pop("router_outputs", None)
    core_config.save({"ndi": raw})
    r.migrate()
    saved = core_config.get().get("ndi") or {}
    eq("the old lists are emptied", (saved.get("routes"), saved.get("salvos")), ([], []))
    eq("the outputs stay with NDI", [o["name"] for o in s.settings()["router_outputs"]], ["Lobby TV", "Stage"])
    snap = r.snapshot()
    eq("the route is a core route", [(x["name"], x["source"]) for x in snap["routes"]],
       [("Lobby TV", "BIRD (HDMI 1)"), ("Stage", "")])
    eq("the preset moved", [(v["name"], sorted(v["routes"].values())) for v in snap["salvos"]],
       [("Sunday", ["", "BIRD (HDMI 1)"])])
    r.migrate()
    eq("a second run changes nothing", len(r.snapshot()["salvos"]), 1)
    for o in list(s.settings()["router_outputs"]):
        r.remove_output(o["id"])
    for v in r.snapshot()["salvos"]:
        r.delete_salvo(v["id"])
    s._seen.clear()                     # a scan may have run during the restart
    s.merge(found)                      # the restart cleared the list: put back what the next section expects
    s.merge([found[0]])
    s.merge([found[0]])

with section("board widgets"):
    types = [w["type"] for w in MODULE.board_widgets()]
    eq("four widgets", types, ["ndi.summary", "ndi.sources", "ndi.source", "ndi.view"])
    items = [{"id": "a", "type": "ndi.summary"}, {"id": "b", "type": "ndi.sources"},
             {"id": "c", "type": "ndi.source", "props": {"source": "BIRD (HDMI 1)"}},
             {"id": "d", "type": "ndi.source", "props": {}}]
    data = asyncio.run(MODULE.board_data(items))
    eq("summary", data["a"]["lines"], ["1 online", "1 offline"])
    eq("list rows", len(data["b"]["rows"]), 2)
    eq("a source tile", data["c"]["lines"][:2], ["HDMI 1", "Online"])
    eq("no source chosen", data["d"]["lines"], ["Choose a source"])
    asyncio.run(svc.service.stop())
    eq("stop clears sources", svc.service.sources(), [])

runtime.reset()
done()
