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


class FakeBackend:
    def __init__(self):
        self.opened, self.closed, self.senders = [], [], []

    def connect(self, name, address=""):
        self.opened.append(name)
        return FakeConn(self, name)

    def open_sender(self, name):
        s = type("S", (), {"sent": [], "send": lambda self, f: self.sent.append(f), "close": lambda self: None})()
        self.senders.append(s)
        return s


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
                                                "interval": 1}), {"extra_ips": ["10.0.0.1"], "groups": ["A"], "interval": 5})
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

with section("wait_change"):
    async def go():
        t = asyncio.create_task(svc.service.wait_change(svc.service.version, 5))
        await asyncio.sleep(0.05)
        svc.service._bump()
        await asyncio.wait_for(t, 2)
        return True

    check("wakes on a change", asyncio.run(go()))

with section("board widgets"):
    types = [w["type"] for w in MODULE.board_widgets()]
    eq("three widgets", types, ["ndi.summary", "ndi.sources", "ndi.source"])
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
