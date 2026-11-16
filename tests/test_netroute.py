"""Traffic classes, port routes and the resolve API (app/netroute.py, #344). Offline, no second NIC needed.
Run: .venv/bin/python tests/test_netroute.py"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(TMP, "certs")
os.environ["RELAY_ANNOUNCE"] = "0"

from app import config, netinfo, netroute  # noqa: E402
from app.modules import Health, Manifest, Module, Traffic, modules, validate  # noqa: E402
from app.netinfo import Port  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section  # noqa: E402

WIFI = Port(id="mac:aa:00:00:00:00:01", name="en0", friendly_name="Wi-Fi", ipv4=("192.168.1.20/24",),
            default_route=True, up=True, kind="wifi")
USB = Port(id="mac:aa:00:00:00:00:05", name="en5", friendly_name="USB 10/100/1000 LAN", ipv4=("10.0.0.5/24",),
           up=True, kind="ethernet")
DANTE_NET = Port(id="mac:aa:00:00:00:00:06", name="en6", friendly_name="Thunderbolt Ethernet",
                 ipv4=("169.254.20.7/16",), up=True, kind="ethernet", link_local_only=True)


class NetFake(Module):
    manifest = Manifest(id="netfake", name="Net fake", version="1.0.0", summary="A module with traffic.",
                        settings_key="netfake",
                        traffic=(Traffic("netfake", "Fake control"), Traffic("netfake.audio", "Fake audio", media=True),
                                 Traffic("netfake.excl", "Fake exclusive", exclusive=True)))

    def __init__(self):
        self.calls = []
        self.state = Health("ok", "Connected.")

    def network_changed(self, resolved):
        self.calls.append(resolved)

    def health(self):
        return self.state


class Exclusive2(Module):
    manifest = Manifest(id="netfake2", name="Net fake 2", version="1.0.0", summary="Another.", settings_key="netfake2",
                        traffic=(Traffic("netfake2", "Other exclusive", exclusive=True),))


FAKE = NetFake()
modules.all()
modules._modules["netfake"] = FAKE
modules._modules["netfake2"] = Exclusive2()


def use_ports(*ps):
    netroute.update(list(ps))


def lan(*ips):
    return mock.patch.object(netinfo, "lan_ips", lambda: list(ips))


config.ensure_file()

with section("an install with no network config behaves as before"):
    cfg = config.load()
    eq("default network section", cfg["network"], {"routes": {}, "policy": {}, "known_ports": {}})
    use_ports(WIFI, USB)
    with lan("192.168.1.20", "10.0.0.5"):
        r = netroute.resolve("crew")
    eq("no route: the system default address", (r.port_id, r.address, r.available, r.fallback), (None, "192.168.1.20", True, False))
    eq("the default route's port is named", r.port.id, WIFI.id)
    with lan():
        eq("no network at all: unavailable, said plainly", (netroute.resolve("viewers").available, netroute.resolve("viewers").reason),
           (False, "This computer has no network address."))
    _raises = True
    try:
        netroute.resolve("nosuch")
        _raises = False
    except KeyError:
        pass
    check("resolve('nosuch') raises KeyError", _raises)

with section("classes: core's and each module's"):
    cls = netroute.classes()
    eq("core first, then modules", list(cls)[:3], ["viewers", "crew", "discovery"])
    eq("module classes with their owner", (cls["netfake.audio"][0], cls["netfake.audio"][1].default_policy), ("netfake", "strict"))
    eq("non-media defaults to fallback", cls["netfake"][1].default_policy, "fallback")
    eq("a good manifest validates", [p for p in validate(FAKE, "netfake") if "traffic" in p], [])

    class Bad(Module):
        manifest = Manifest(id="badnet", name="B", version="1.0.0", summary="B", settings_key="badnet",
                            traffic=(Traffic("dante", "Not mine"), Traffic("badnet.x", ""), Traffic("badnet.x", "Twice"), "nope"))

    probs = [p for p in validate(Bad(), "badnet") if "traffic" in p]
    eq("ids must belong to the module, need a label, appear once, be Traffic", len(probs), 4)

with section("remembering ports"):
    eq("new ports are written once", netroute.remember([WIFI, USB]), True)
    eq("seen again: nothing written", netroute.remember([WIFI, USB]), False)
    known = config.get()["network"]["known_ports"]
    eq("names kept", known[USB.id], {"name": "en5", "friendly_name": "USB 10/100/1000 LAN", "kind": "ethernet"})
    renamed = Port(**{**USB.__dict__, "name": "en7"})
    eq("a rename is written", netroute.remember([renamed]), True)
    eq("still one entry per MAC", config.get()["network"]["known_ports"][USB.id]["name"], "en7")
    netroute.remember([DANTE_NET])

with section("a routed class resolves to its port"):
    config.save({"network": {"routes": {"netfake.audio": DANTE_NET.id, "crew": USB.id}}})
    use_ports(WIFI, USB, DANTE_NET)
    with lan("192.168.1.20", "10.0.0.5"):
        r = netroute.resolve("netfake.audio")
        eq("a link-local-only port is usable (a Dante network with no DHCP)", (r.address, r.available, r.routed), ("169.254.20.7", True, True))
        eq("crew on the USB port", netroute.resolve("crew").address, "10.0.0.5")
        eq("an unrouted class still follows the default", netroute.resolve("netfake").address, "192.168.1.20")
        eq("as_dict is plain JSON", json.loads(json.dumps(r.as_dict()))["port"]["id"], DANTE_NET.id)

with section("policy when the port is missing"):
    with lan("192.168.1.20"):
        use_ports(WIFI, USB)                    # the Dante adapter is unplugged
        r = netroute.resolve("netfake.audio")
        eq("strict (media default): not run, never moved to another network",
           (r.available, r.address, r.fallback), (False, None, False))
        eq("the reason names the port", r.reason, "Fake audio: Thunderbolt Ethernet is not connected to this computer.")
        down = Port(**{**USB.__dict__, "up": False, "ipv4": ()})
        use_ports(WIFI, down)
        r = netroute.resolve("crew")
        eq("fallback (crew default): the default address, said why",
           (r.available, r.address, r.fallback), (True, "192.168.1.20", True))
        check("the reason says the port is down and the default is used",
              "is down" in r.reason and "default network instead" in r.reason, r.reason)
        noaddr = Port(**{**USB.__dict__, "ipv4": ()})
        use_ports(WIFI, noaddr)
        config.save({"network": {"policy": {"crew": "strict"}}})
        r = netroute.resolve("crew")
        eq("a policy set to strict: link up with no address is unavailable", (r.available, r.reason),
           (False, "Operator panel, sign-in and crew pages (HTTPS): USB 10/100/1000 LAN has no network address."))
        config.save({"network": {"policy": {"netfake.audio": "fallback"}}})
        use_ports(WIFI)
        eq("media switched to fallback uses the default", netroute.resolve("netfake.audio").address, "192.168.1.20")
        config.save({"network": {"policy": {"crew": "fallback", "netfake.audio": "strict"}}})
        eq("a policy equal to the default is not stored", config.get()["network"]["policy"], {})

with section("core puts a strict class's missing port on the module's health"):
    modules_cfg = {"modules": {"netfake": {"enabled": True}}}
    config.save(modules_cfg)
    use_ports(WIFI, USB)
    with lan("192.168.1.20"):
        h = modules.health(FAKE)
        eq("warn, with the port's sentence first", (h.state, h.detail),
           ("warn", "Fake audio: Thunderbolt Ethernet is not connected to this computer. Connected."))
        FAKE.state = Health("error", "Login failed.")
        eq("an error stays an error", modules.health(FAKE).state, "error")
        FAKE.state = Health("ok", "Connected.")
        use_ports(WIFI, USB, DANTE_NET)
        eq("port back: the module's own health", modules.health(FAKE), Health("ok", "Connected."))

with section("clean(): round trip, stale ports and clashes"):
    raw = {"routes": {"netfake.audio": DANTE_NET.id, "crew": USB.id}, "policy": {"netfake.audio": "fallback"},
           "known_ports": config.get()["network"]["known_ports"]}
    eq("a clean config round-trips unchanged", netroute.clean(json.loads(json.dumps(raw))), raw)
    eq("cleaning twice changes nothing", netroute.clean(netroute.clean(raw)), raw)
    got = netroute.clean({"routes": {"netfake": "mac:ff:ff:ff:ff:ff:ff", "nosuch": USB.id, "Bad Id!": USB.id,
                                     "crew": 7}, "policy": {"crew": "sometimes", "nosuch": "strict"},
                          "known_ports": raw["known_ports"]})
    eq("never-seen port, unknown class, bad id and non-string port all dropped", got["routes"], {})
    eq("bad policies dropped", got["policy"], {})
    notes = netroute.notices()
    check("a notice for the never-seen port, not an error",
          any("Fake control was set to a network port Relay has never seen" in n for n in notes), notes)
    check("a notice for the unknown class", any("'nosuch'" in n for n in notes), notes)
    clash = netroute.clean({"routes": {"netfake.excl": USB.id, "netfake2": USB.id, "netfake": USB.id},
                            "known_ports": raw["known_ports"]})
    eq("two exclusive classes on one port: the second refused, a shared one kept", clash["routes"],
       {"netfake.excl": USB.id, "netfake": USB.id})
    check("the clash is explained", any("needs a network port of its own" in n for n in netroute.notices()))
    eq("route_problems refuses the same for a page that saves routes",
       list(netroute.route_problems({"netfake.excl": USB.id, "netfake2": USB.id}, raw["known_ports"])), ["netfake2"])
    eq("junk known_ports are dropped", netroute.clean({"known_ports": {"no-prefix": {}, "mac:ok": "x"}})["known_ports"],
       {"mac:ok": {"name": "", "friendly_name": "", "kind": ""}})
    eq("not a dict at all", netroute.clean("nope"), {"routes": {}, "policy": {}, "known_ports": {}})
    stored = json.loads(Path(os.environ["RELAY_CONFIG"]).read_text())["network"]
    eq("config.json keeps what clean() keeps", stored["routes"], {"netfake.audio": DANTE_NET.id, "crew": USB.id})

with section("a running module hears its port go away and come back"):
    config.save({"network": {"policy": {"netfake.audio": "strict"}}})
    FAKE.calls.clear()
    netroute._last.clear()
    with lan("192.168.1.20"):
        use_ports(WIFI, USB, DANTE_NET)
        eq("not running: not told", netroute.refresh(), [])
        modules._running.add("netfake")
        eq("just started: recorded, not told", netroute.refresh(), [])
        eq("nothing changed: not told", netroute.refresh(), [])
        with mock.patch.object(netroute, "remember", return_value=False):
            netroute.on_network_change([WIFI, USB])           # the Dante adapter unplugged
        eq("told once", len(FAKE.calls), 1)
        gone = FAKE.calls[-1]["netfake.audio"]
        eq("with every class of the module, the lost one unavailable", (sorted(FAKE.calls[-1]), gone.available),
           (["netfake", "netfake.audio", "netfake.excl"], False))
        with mock.patch.object(netroute, "remember", return_value=False):
            netroute.on_network_change([WIFI, USB, DANTE_NET])
        eq("told it came back", (len(FAKE.calls), FAKE.calls[-1]["netfake.audio"].available), (2, True))
        moved = Port(**{**DANTE_NET.__dict__, "ipv4": ("169.254.99.1/16",)})
        with mock.patch.object(netroute, "remember", return_value=False):
            netroute.on_network_change([WIFI, USB, moved])
        eq("told the address changed", FAKE.calls[-1]["netfake.audio"].address, "169.254.99.1")
        config.save({"network": {"routes": {"netfake": USB.id}}})
        netroute.refresh()
        eq("told when the operator routes a class elsewhere", FAKE.calls[-1]["netfake"].address, "10.0.0.5")
        n = len(FAKE.calls)
        FAKE.network_changed = lambda resolved: (_ for _ in ()).throw(RuntimeError("boom"))
        config.save({"network": {"routes": {"netfake": WIFI.id}}})
        eq("a module that raises does not stop the others", netroute.refresh(), [])
        del FAKE.network_changed
        modules._running.discard("netfake")
        netroute.refresh()
        check("stopped: forgotten, so a restart starts fresh", "netfake" not in netroute._last)
        eq("no calls while stopped", len(FAKE.calls), n)

with section("main hands every network change to the routes"):
    from app import main
    seen = []
    with mock.patch.object(netroute, "on_network_change", seen.append), \
            mock.patch.object(main.sidecar, "emit"), mock.patch.object(main.tls.panel, "refresh", return_value=False):
        main._on_lan_change(netinfo.Change(["192.168.1.20"], [WIFI], ips_changed=False))
    eq("ports passed on even when the addresses did not change", seen, [[WIFI]])
    with mock.patch.object(netinfo, "interfaces", return_value=[WIFI, USB]), \
            mock.patch.object(netroute, "remember", side_effect=RuntimeError("disk full")):
        asyncio.run(asyncio.to_thread(netroute.startup))
    eq("startup keeps the ports even when remembering fails", netroute.ports(), [WIFI, USB])

done()
