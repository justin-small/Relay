"""Network ports and LAN addresses (app/netinfo.py). Offline. Run: .venv/bin/python tests/test_netinfo.py"""
import asyncio
import os
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("RELAY_ADMIN_IPS", None)

from app import netinfo  # noqa: E402
from app.netinfo import _Adapter, _Link  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import await_for, check, done, eq, section, tmpdir  # noqa: E402


def adapter(*addrs, name="", nice=""):
    return types.SimpleNamespace(name=name, nice_name=nice or name, ips=[
        types.SimpleNamespace(ip=a, is_IPv4=True, network_prefix=24) if isinstance(a, str)
        else types.SimpleNamespace(ip=a[0], is_IPv4=False, network_prefix=64) for a in addrs])


def fake_ifaddr(*adapters):
    return mock.patch.dict(sys.modules, {"ifaddr": types.SimpleNamespace(get_adapters=lambda: list(adapters))})


def ports(adapters, links, default_ip=None, include_hidden=False):
    """interfaces() over a faked adapter list and faked OS link records."""
    with mock.patch.object(netinfo, "_adapters", return_value=adapters), \
            mock.patch.object(netinfo, "_links", return_value=links), \
            mock.patch.object(netinfo, "_default_route_ip", return_value=default_ip):
        return netinfo.interfaces(include_hidden)


def by_name(ps):
    return {p.name: p for p in ps}


with section("lan_ips keeps working for existing callers"):
    with fake_ifaddr(adapter("127.0.0.1", name="lo0"), adapter("169.254.3.4", name="en7"),
                     adapter("192.168.1.20", name="en0"), adapter("10.0.0.5", name="en5"),
                     adapter(("fe80::1", 0, 0), name="utun0"), adapter("192.168.1.20", name="bridge0")):
        eq("loopback, link-local, IPv6 and duplicates dropped", netinfo._interface_ips(), ["192.168.1.20", "10.0.0.5"])
        with mock.patch.object(netinfo, "_default_route_ip", return_value="10.0.0.5"):
            eq("default route first", netinfo.lan_ips(), ["10.0.0.5", "192.168.1.20"])
        with mock.patch.object(netinfo, "_default_route_ip", return_value=None), \
                mock.patch.dict(os.environ, {"RELAY_ADMIN_IPS": "203.0.113.9; nope,192.168.1.20"}):
            eq("extras appended, junk and repeats ignored", netinfo.lan_ips(), ["192.168.1.20", "10.0.0.5", "203.0.113.9"])
        with mock.patch.object(netinfo, "_default_route_ip", return_value="127.0.0.1"):
            eq("a loopback default route is not a LAN address", netinfo.lan_ips(), ["192.168.1.20", "10.0.0.5"])
        eq("adapter records carry the prefix", netinfo._adapters()[2], _Adapter("en0", "en0", (("192.168.1.20", 24),)))
    with mock.patch.object(netinfo, "_interface_ips", return_value=[]), mock.patch.object(netinfo, "_default_route_ip", return_value=None):
        eq("no network gives an empty list", netinfo.lan_ips(), [])
    with mock.patch.object(netinfo, "_links", side_effect=AssertionError("lan_ips must not read link state")), \
            fake_ifaddr(adapter("192.168.1.20", name="en0")), mock.patch.object(netinfo, "_default_route_ip", return_value=None):
        eq("lan_ips never starts the link-state commands", netinfo.lan_ips(), ["192.168.1.20"])

ETH5 = _Link("en5", "en5", "USB 10/100/1000 LAN", mac="aa:bb:cc:00:00:05", up=True, speed_mbps=1000, kind="ethernet")
WIFI = _Link("en0", "en0", "Wi-Fi", mac="aa:bb:cc:00:00:00", up=True, kind="wifi")

with section("two ports"):
    ps = ports([_Adapter("en0", "en0", (("192.168.1.20", 24),)), _Adapter("en5", "en5", (("10.0.0.5", 16),))],
               [WIFI, ETH5], default_ip="192.168.1.20")
    eq("one record per port", [p.name for p in ps], ["en0", "en5"])
    eq("ids are the MACs", [p.id for p in ps], ["mac:aa:bb:cc:00:00:00", "mac:aa:bb:cc:00:00:05"])
    eq("addresses with prefix", ps[1].ipv4, ("10.0.0.5/16",))
    eq("default route marked on its port only", [p.default_route for p in ps], [True, False])
    eq("friendly name, link and speed from the OS", (ps[1].friendly_name, ps[1].up, ps[1].speed_mbps, ps[1].kind),
       ("USB 10/100/1000 LAN", True, 1000, "ethernet"))
    check("as_dict is plain JSON data", ps[1].as_dict()["ipv4"] == ["10.0.0.5/16"])

with section("an unplugged port is listed, down; a port that goes away is reported"):
    down = _Link("en5", "en5", "USB 10/100/1000 LAN", mac="aa:bb:cc:00:00:05", up=False, kind="ethernet")
    before = ports([_Adapter("en0", "en0", (("192.168.1.20", 24),)), _Adapter("en5", "en5", (("10.0.0.5", 16),))], [WIFI, ETH5])
    pulled = ports([_Adapter("en0", "en0", (("192.168.1.20", 24),))], [WIFI, down])
    eq("cable pulled: still listed with no address, link down", (by_name(pulled)["en5"].ipv4, by_name(pulled)["en5"].up), ((), False))
    added, removed, changed = netinfo.diff_ports(before, pulled)
    eq("cable pulled is a change, not a removal", ([p.name for p in removed], [b.name for b, _ in changed]), ([], ["en5"]))
    gone = ports([_Adapter("en0", "en0", (("192.168.1.20", 24),))], [WIFI])
    added, removed, changed = netinfo.diff_ports(before, gone)
    eq("adapter unplugged: removed", ([p.id for p in removed], added, changed), (["mac:aa:bb:cc:00:00:05"], [], []))
    added, removed, changed = netinfo.diff_ports(gone, before)
    eq("adapter plugged back in: added", [p.name for p in added], ["en5"])

with section("a renamed port with the same MAC"):
    moved = _Link("en7", "en7", "USB 10/100/1000 LAN", mac="aa:bb:cc:00:00:05", up=True, speed_mbps=1000, kind="ethernet")
    after = ports([_Adapter("en7", "en7", (("10.0.0.5", 16),))], [moved])
    before = ports([_Adapter("en5", "en5", (("10.0.0.5", 16),))], [ETH5])
    eq("same id after the rename", after[0].id, before[0].id)
    added, removed, changed = netinfo.diff_ports(before, after)
    eq("a rename is a change", (added, removed, [(b.name, a.name) for b, a in changed]), ([], [], [("en5", "en7")]))
    eq("described as a rename", netinfo._describe(*changed[0]), "renamed en5 -> en7")
    nomac = ports([_Adapter("eth9", "eth9", (("10.0.0.5", 16),))], [])
    eq("no MAC: the name is the id", nomac[0].id, "name:eth9")

with section("a link-local-only port"):
    dante = _Link("en6", "en6", "Thunderbolt Ethernet", mac="aa:bb:cc:00:00:06", up=True, kind="ethernet")
    p = ports([_Adapter("en6", "en6", (("169.254.20.7", 16),))], [dante])[0]
    check("listed and marked link-local only", p.link_local_only and not p.virtual)
    with mock.patch.object(netinfo, "_adapters", return_value=[_Adapter("en6", "en6", (("169.254.20.7", 16),))]), \
            mock.patch.object(netinfo, "_default_route_ip", return_value=None):
        eq("not offered as a viewer address", netinfo.lan_ips(), [])
    mixed = ports([_Adapter("en6", "en6", (("169.254.20.7", 16), ("10.0.0.9", 8)))], [dante])[0]
    check("a port with a routable address too is not link-local only", not mixed.link_local_only)

with section("a VPN adapter"):
    vpn = _Link("utun4", "utun4", "utun4", up=True, kind="vpn", virtual=True, vpn=True)
    ps = ports([_Adapter("utun4", "utun4", (("100.64.1.2", 32),))], [vpn, _Link("utun0", "utun0", "utun0", kind="vpn", virtual=True, vpn=True)])
    eq("the VPN with an address is listed, flagged; the idle tunnel is hidden",
       [(p.name, p.vpn, p.virtual, p.id) for p in ps], [("utun4", True, True, "name:utun4")])
    guessed = ports([_Adapter("wg0", "wg0", (("10.8.0.2", 24),))], [])[0]
    eq("with no OS record a VPN is guessed from its name", (guessed.kind, guessed.vpn), ("vpn", True))

with section("loopback and the OS's own interfaces are hidden"):
    links = [_Link("lo0", "lo0", "lo0", kind="loopback", virtual=True, loopback=True),
             _Link("awdl0", "awdl0", "awdl0", mac="aa:bb:cc:00:00:aa", up=True, kind="virtual", virtual=True),
             _Link("bridge0", "bridge0", "Thunderbolt Bridge", mac="aa:bb:cc:00:00:01", up=False, kind="bridge", virtual=True),
             _Link("en1", "en1", "Thunderbolt 1", mac="aa:bb:cc:00:00:01", up=False, kind="ethernet"),
             _Link("ap1", "ap1", "ap1", kind="virtual", virtual=True), WIFI]
    adapters = [_Adapter("lo0", "lo0", (("127.0.0.1", 8),)), _Adapter("en0", "en0", (("192.168.1.20", 24),))]
    eq("shown", [p.name for p in ports(adapters, links)], ["en0", "en1"])
    eq("include_hidden lists them all", sorted(p.name for p in ports(adapters, links, include_hidden=True)),
       ["ap1", "awdl0", "bridge0", "en0", "en1", "lo0"])
    every = by_name(ports(adapters, links, include_hidden=True))
    eq("a bridge sharing a member's MAC is keyed by name", (every["bridge0"].id, every["en1"].id),
       ("name:bridge0", "mac:aa:bb:cc:00:00:01"))
    check("loopback flagged", every["lo0"].loopback)
    shown = ports([_Adapter("docker0", "docker0", (("172.17.0.1", 16),))],
                  [_Link("docker0", "docker0", "docker0", kind="bridge", virtual=True)])
    eq("a virtual port with a LAN address stays listed, as lan_ips lists it", [p.name for p in shown], ["docker0"])
    twins = ports([], [_Link("en8", "en8", "A", mac="aa:bb:cc:00:00:08", kind="ethernet"),
                       _Link("en9", "en9", "B", mac="aa:bb:cc:00:00:08", kind="ethernet")])
    eq("two hardware ports with one MAC do not share an id", [p.id for p in twins],
       ["mac:aa:bb:cc:00:00:08/en8", "mac:aa:bb:cc:00:00:08/en9"])

IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384 index 1
\toptions=1203<RXCSUM,TXCSUM,TXSTATUS,SW_TIMESTAMP>
\tinet 127.0.0.1 netmask 0xff000000
\tstatus: active
\ttype: Loopback
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500 index 14
\tether 84:2f:57:79:98:54
\tinet 192.168.1.206 netmask 0xffffff00 broadcast 192.168.1.255
\tmedia: autoselect
\tstatus: active
\ttype: Wi-Fi
\tagent domain:Skywalk type:NetIf flags:0xa443 desc:"Userspace Networking"
en4: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500 index 7
\tether f2:91:8b:ee:28:3c
\tmedia: none
\tstatus: inactive
\ttype: USB Ethernet
\tagent domain:Skywalk type:NetIf flags:0xa443 desc:"Userspace Networking"
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500 index 8
\tether f2:91:8b:ee:28:3d
\tmedia: autoselect (1000baseT <full-duplex,flow-control>)
\tstatus: active
\ttype: USB Ethernet
en9: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500 index 9
\tether f2:91:8b:ee:28:3f
\tmedia: autoselect (10Gbase-T <full-duplex>)
\tstatus: active
bridge0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 36:98:8b:7a:b0:00
\tmedia: <unknown type>
\tstatus: inactive
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380 index 21
\tinet 100.64.1.2 --> 100.64.1.2 netmask 0xffffffff
awdl0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> mtu 1500 index 16
\tether 56:24:df:49:bc:4c
\tstatus: active
"""
NETWORKSETUP = """\

Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: f2:91:8b:ee:28:3c

Hardware Port: USB 10/100/1000 LAN
Device: en5
Ethernet Address: f2:91:8b:ee:28:3d

Hardware Port: 10GbE
Device: en9
Ethernet Address: f2:91:8b:ee:28:3f

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 36:98:8b:7a:b0:00

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 84:2f:57:79:98:54

VLAN Configurations
===================
"""

with section("macOS: ifconfig -v and networksetup output"):
    mac = {link.name: link for link in netinfo._mac_links_from(IFCONFIG, NETWORKSETUP)}
    eq("every interface read", sorted(mac), ["awdl0", "bridge0", "en0", "en4", "en5", "en9", "lo0", "utun3"])
    eq("Wi-Fi", (mac["en0"].kind, mac["en0"].mac, mac["en0"].up, mac["en0"].friendly_name, mac["en0"].speed_mbps),
       ("wifi", "84:2f:57:79:98:54", True, "Wi-Fi", None))
    eq("unplugged USB Ethernet", (mac["en4"].kind, mac["en4"].up, mac["en4"].virtual), ("ethernet", False, False))
    eq("speed from the media line", (mac["en5"].speed_mbps, mac["en9"].speed_mbps), (1000, 10000))
    eq("hardware port name is the friendly name", mac["en5"].friendly_name, "USB 10/100/1000 LAN")
    eq("Thunderbolt Bridge is a virtual bridge", (mac["bridge0"].kind, mac["bridge0"].virtual), ("bridge", True))
    eq("utun is a VPN, up from RUNNING", (mac["utun3"].kind, mac["utun3"].vpn, mac["utun3"].up, mac["utun3"].mac), ("vpn", True, True, None))
    eq("loopback", (mac["lo0"].kind, mac["lo0"].loopback), ("loopback", True))
    eq("awdl is the OS's own", (mac["awdl0"].kind, mac["awdl0"].virtual), ("virtual", True))
    eq("garbage in, nothing out", netinfo._mac_links_from("not ifconfig\n", ""), [])

with section("macOS: the commands rerun only when an address changes or the cache is old"):
    calls = []

    def fake_run(args):
        calls.append(args[0])
        return IFCONFIG if args[0] == netinfo._IFCONFIG else NETWORKSETUP

    netinfo._mac_cache.update(signature=None, at=0.0, links=[])
    a1 = [_Adapter("en0", "en0", (("192.168.1.206", 24),))]
    a2 = [_Adapter("en0", "en0", (("192.168.1.206", 24),)), _Adapter("en5", "en5", (("169.254.1.9", 16),))]
    with mock.patch.object(netinfo, "_run", side_effect=fake_run):
        first = netinfo._mac_links(a1)
        netinfo._mac_links(a1)
        eq("two polls, one run of each command", len(calls), 2)
        netinfo._mac_links(a2)
        eq("an address change reruns them", len(calls), 4)
        netinfo._mac_cache["at"] -= netinfo.MAC_REFRESH_SECONDS + 1
        netinfo._mac_links(a2)
        eq("an old cache reruns them", len(calls), 6)
    with mock.patch.object(netinfo, "_run", return_value=""):
        netinfo._mac_cache["signature"] = None
        eq("a failed ifconfig keeps the last good answer", len(netinfo._mac_links(a1)), len(first))
    netinfo._mac_cache.update(signature=None, at=0.0, links=[])
    with mock.patch.object(netinfo.subprocess, "run", side_effect=OSError("no such file")):
        eq("a command that cannot run gives no output", netinfo._run(["/nonexistent"]), "")


def sysfs(root, name, **files):
    d = os.path.join(root, name)
    os.makedirs(d)
    for f, value in files.items():
        if value is None:
            os.makedirs(os.path.join(d, f))
        else:
            with open(os.path.join(d, f), "w") as fh:
                fh.write(value + "\n")


with section("Linux: /sys/class/net"):
    root = tmpdir()
    sysfs(root, "lo", address="00:00:00:00:00:00", type="772", operstate="unknown", carrier="1")
    sysfs(root, "eth0", address="AA:BB:CC:00:00:01", type="1", operstate="up", speed="1000", device=None)
    sysfs(root, "enx001122", address="00:11:22:33:44:55", type="1", operstate="down", speed="-1", device=None)
    sysfs(root, "wlan0", address="aa:bb:cc:00:00:02", type="1", operstate="dormant", device=None, wireless=None)
    sysfs(root, "docker0", address="02:42:ac:11:00:01", type="1", operstate="down", bridge=None)
    sysfs(root, "veth1a2b", address="aa:bb:cc:00:00:03", type="1", operstate="up", speed="10000")
    sysfs(root, "tun0", type="65534", operstate="unknown", carrier="1", tun_flags="0x1001")
    sysfs(root, "wg0", type="65534", operstate="unknown")
    lx = {link.name: link for link in netinfo._linux_links(root)}
    eq("ethernet", (lx["eth0"].kind, lx["eth0"].mac, lx["eth0"].up, lx["eth0"].speed_mbps, lx["eth0"].virtual),
       ("ethernet", "aa:bb:cc:00:00:01", True, 1000, False))
    eq("unplugged USB adapter: down, no speed", (lx["enx001122"].up, lx["enx001122"].speed_mbps), (False, None))
    eq("Wi-Fi", (lx["wlan0"].kind, lx["wlan0"].up), ("wifi", False))
    eq("loopback has no MAC", (lx["lo"].kind, lx["lo"].mac, lx["lo"].up), ("loopback", None, True))
    eq("bridge", (lx["docker0"].kind, lx["docker0"].virtual), ("bridge", True))
    eq("a veth is virtual", (lx["veth1a2b"].kind, lx["veth1a2b"].virtual), ("virtual", True))
    eq("tun is a VPN, up from its carrier", (lx["tun0"].kind, lx["tun0"].vpn, lx["tun0"].up), ("vpn", True, True))
    eq("WireGuard with no carrier file: link state unknown", (lx["wg0"].vpn, lx["wg0"].up), (True, None))
    eq("a missing sysfs gives nothing", netinfo._linux_links(os.path.join(root, "missing")), [])
    with mock.patch.object(netinfo.sys, "platform", "linux"), mock.patch.object(netinfo, "_linux_links", lambda: list(lx.values())):
        ps = ports([_Adapter("eth0", "eth0", (("10.0.0.5", 24),)), _Adapter("docker0", "docker0", (("172.17.0.1", 16),))],
                   netinfo._links([]))
        eq("Linux ports: veth, loopback and idle tunnels hidden", [p.name for p in ps], ["eth0", "docker0", "enx001122", "wlan0"])

with section("Windows: GetAdaptersAddresses fields"):
    eth = netinfo._win_link("{4D36E972-1}", "Realtek USB GbE Family Controller", "Ethernet 2",
                            bytes([0xAA, 0xBB, 0xCC, 0, 0, 9]), 6, 1, 1_000_000_000)
    eq("ethernet", (eth.key, eth.name, eth.friendly_name, eth.mac, eth.up, eth.speed_mbps, eth.kind),
       ("{4D36E972-1}", "Ethernet 2", "Realtek USB GbE Family Controller", "aa:bb:cc:00:00:09", True, 1000, "ethernet"))
    wifi = netinfo._win_link("{W}", "Intel(R) Wi-Fi 6 AX201", "Wi-Fi", bytes(6), 71, 2, 0)
    eq("Wi-Fi down, all-zero MAC dropped, no speed", (wifi.kind, wifi.up, wifi.mac, wifi.speed_mbps), ("wifi", False, None, None))
    unknown_speed = netinfo._win_link("{U}", "USB Ethernet", "Ethernet 3", b"", 6, 7, 2 ** 64 - 1)
    eq("lower layer down, unknown speed", (unknown_speed.up, unknown_speed.speed_mbps, unknown_speed.mac), (False, None, None))
    vpn = netinfo._win_link("{V}", "WireGuard Tunnel", "office", b"", 53, 1, 0)
    eq("WireGuard is a VPN", (vpn.kind, vpn.vpn, vpn.virtual), ("vpn", True, True))
    hv = netinfo._win_link("{H}", "Hyper-V Virtual Ethernet Adapter", "vEthernet (Default Switch)", bytes([0, 0x15, 0x5d, 1, 2, 3]), 6, 1, 10_000_000_000)
    eq("Hyper-V switch is virtual", (hv.kind, hv.virtual, hv.speed_mbps), ("virtual", True, 10000))
    lo = netinfo._win_link("{L}", "Software Loopback Interface 1", "Loopback Pseudo-Interface 1", b"", 24, 1, 0)
    eq("loopback", (lo.kind, lo.loopback), ("loopback", True))
    teredo = netinfo._win_link("{T}", "Teredo Tunneling Pseudo-Interface", "Teredo", b"", 131, 4, 0)
    eq("Teredo is virtual, link state unknown", (teredo.kind, teredo.up), ("virtual", None))

with section("this machine, read for real (each CI platform runs its own branch)"):
    real = netinfo.interfaces(include_hidden=True)
    check("a list of ports", isinstance(real, list) and all(isinstance(p, netinfo.Port) for p in real))
    check("ids are unique", len({p.id for p in real}) == len(real), [p.id for p in real])
    check("kinds are known", all(p.kind in netinfo.KINDS for p in real), sorted({p.kind for p in real}))
    lan = set(netinfo._interface_ips())
    listed = {a for p in netinfo.interfaces() for a in p.addresses}
    check("every lan_ips address belongs to a listed port", lan <= listed, sorted(lan - listed))

with section("watch reports address changes and port changes"):
    seen = []
    p0 = netinfo.Port(id="mac:aa", name="en5", friendly_name="USB", ipv4=("10.0.0.5/16",), up=True)
    p1 = netinfo.Port(id="mac:aa", name="en5", friendly_name="USB", up=False)
    p2 = netinfo.Port(id="mac:bb", name="en6", friendly_name="TB", ipv4=("169.254.1.2/16",), up=True, link_local_only=True)
    seq = iter([(["192.168.1.20"], [p0]), (["192.168.1.20"], [p0]), (["10.1.1.7"], [p0]),
                (["10.1.1.7"], [p1]), (["10.1.1.7"], [p1, p2]), (["10.1.1.7"], [p2])])

    async def run():
        with mock.patch.object(netinfo, "_read_network", lambda: next(seq, (["10.1.1.7"], [p2]))):
            task = asyncio.create_task(netinfo.watch(seen.append, interval=0.01))
            await await_for(lambda: len(seen) >= 4)
            await asyncio.sleep(0.05)  # and nothing more once the network settles
            task.cancel()

    asyncio.run(run())
    eq("one call per change", len(seen), 4)
    eq("Wi-Fi switch: the addresses", (seen[0].ips, seen[0].ips_changed, seen[0].changed), (["10.1.1.7"], True, []))
    eq("cable pulled: a port change only", (seen[1].ips_changed, [(b.up, a.up) for b, a in seen[1].changed]), (False, [(True, False)]))
    eq("link-local port plugged in", ([p.name for p in seen[2].added], seen[2].ports), (["en6"], [p1, p2]))
    eq("adapter unplugged", [p.name for p in seen[3].removed], ["en5"])

with section("main's handler tells the desktop shell only of an address change"):
    from app import main
    with mock.patch.object(main.tls.panel, "refresh", return_value=False) as refresh, \
            mock.patch.object(main.sidecar, "emit") as emit:
        main._on_lan_change(netinfo.Change(["10.1.1.7"], [], ips_changed=False, removed=[p0]))
        eq("port change alone: certificate checked (a routed crew port may be link-local), desktop shell untouched",
           (refresh.call_count, emit.call_count), (1, 0))
        main._on_lan_change(netinfo.Change(["10.1.1.7"], [], ips_changed=True))
        eq("address change: desktop shell told", emit.call_args.kwargs, {"ips": ["10.1.1.7"]})

done()
