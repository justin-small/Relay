"""This machine's network ports and LAN addresses, worked out from the network interfaces.

Viewer links, the QR codes and the certificate all need the address a phone on
the same network would dial. Run natively there is no launcher to hand it in, so
read it from the interfaces, and notice when it changes (a laptop moving from one
Wi-Fi network to another, a USB Ethernet adapter plugged in, a cable pulled).

`interfaces()` lists each port as its own record: a stable id (the MAC where the
OS gives one, because a USB adapter's name changes when it moves socket), its
names, IPv4 addresses, link state and speed. `lan_ips()` is the flat address list
the viewer links use, built from the same adapter records.

ifaddr gives the addresses on every OS. The rest comes from the OS itself: files
under /sys/class/net on Linux, `ifconfig -v` and `networksetup` on macOS (cached,
since they are separate processes), GetAdaptersAddresses on Windows.

`RELAY_ADMIN_IPS` is kept as optional extras: addresses the interfaces cannot
show, such as a port forward or a VPN name the operator wants on the page.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import subprocess  # nosec B404 - fixed macOS commands with no user input, see _run
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

log = logging.getLogger("relay.netinfo")

POLL_SECONDS = 5.0
# macOS link state comes from two separate processes; they rerun when an address changes or after this long.
MAC_REFRESH_SECONDS = 30.0

KINDS = ("ethernet", "wifi", "vpn", "bridge", "virtual", "loopback", "other")


@dataclass(frozen=True)
class Port:
    """One network port. `id` stays the same when the OS renames it (it is the MAC when one is known)."""
    id: str
    name: str                      # the system's name: en5, eth0, "Ethernet 2"
    friendly_name: str             # "USB 10/100/1000 LAN", "Realtek USB GbE Family Controller"
    mac: str | None = None
    ipv4: tuple[str, ...] = ()     # interface notation, "192.168.1.20/24"
    default_route: bool = False
    up: bool | None = None         # None when the OS does not say
    speed_mbps: int | None = None
    kind: str = "other"            # one of KINDS
    virtual: bool = False          # not a piece of hardware (VPN, bridge, VM network, loopback)
    vpn: bool = False
    loopback: bool = False
    link_local_only: bool = False  # only 169.254.x.x: no DHCP, as a Dante or NDI network often is

    @property
    def addresses(self) -> list[str]:
        return [a.split("/")[0] for a in self.ipv4]

    def as_dict(self) -> dict:
        out = asdict(self)
        out["ipv4"] = list(self.ipv4)
        return out


@dataclass(frozen=True)
class _Adapter:
    """An adapter as ifaddr reports it: the name the OS joins on, and its IPv4 (address, prefix) pairs."""
    key: str
    nice_name: str
    ipv4: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _Link:
    """What the OS adds to an adapter: names, MAC, link state, speed and what sort of port it is."""
    key: str
    name: str
    friendly_name: str
    mac: str | None = None
    up: bool | None = None
    speed_mbps: int | None = None
    kind: str = "other"
    virtual: bool = False
    vpn: bool = False
    loopback: bool = False


# --- addresses (every OS, ifaddr) ---------------------------------------------------------------------------

def _default_route_ip() -> str | None:
    """The address of the interface that carries the default route.

    connect() on a UDP socket only picks a route and sends nothing, so this
    works on a venue LAN with no internet behind it.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _adapters() -> list[_Adapter]:
    """Every adapter ifaddr can see, with its IPv4 addresses (ifaddr lists only adapters that have one)."""
    try:
        import ifaddr
    except ImportError:  # the app still serves; it falls back to the default route
        return []
    out: list[_Adapter] = []
    for adapter in ifaddr.get_adapters():
        ips: list[tuple[str, int]] = []
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            try:
                ipaddress.ip_address(ip.ip)
            except ValueError:
                continue
            pair = (str(ip.ip), int(getattr(ip, "network_prefix", 32) or 32))
            if pair not in ips:
                ips.append(pair)
        name = str(getattr(adapter, "name", "") or "")
        out.append(_Adapter(name, str(getattr(adapter, "nice_name", "") or name), tuple(ips)))
    return out


def _usable(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast)


def _interface_ips() -> list[str]:
    """Every usable IPv4 address on the machine's interfaces."""
    out: list[str] = []
    for adapter in _adapters():
        for ip, _prefix in adapter.ipv4:
            if _usable(ip) and ip not in out:
                out.append(ip)
    return out


def extra_ips() -> list[str]:
    """RELAY_ADMIN_IPS, parsed; entries that are not IP addresses are ignored."""
    out: list[str] = []
    for raw in (os.environ.get("RELAY_ADMIN_IPS") or "").replace(";", ",").split(","):
        raw = raw.strip()
        try:
            ipaddress.ip_address(raw)
        except ValueError:
            continue
        if raw not in out:
            out.append(raw)
    return out


def lan_ips() -> list[str]:
    """LAN addresses, the default route's first, then the operator's extras.

    Empty when the machine has no network at all; callers fall back to loopback.
    """
    found = _interface_ips()
    first = _default_route_ip()
    if first and not ipaddress.ip_address(first).is_loopback:
        if first in found:
            found.remove(first)
        found.insert(0, first)
    for extra in extra_ips():
        if extra not in found:
            found.append(extra)
    return found


# --- what sort of port a name is, when the OS does not say ---------------------------------------------------

_VPN_NAME = re.compile(r"^(utun|ipsec|ppp|tun|tap|wg|tailscale|zt|nordlynx|vpn)", re.I)
_VIRTUAL_NAME = re.compile(r"^(bridge|br-|docker|veth|vmnet|vboxnet|virbr|vnic|lxc|lxd|cni|flannel|podman)", re.I)
# Interfaces the OS keeps for itself (Apple Wireless Direct, low-latency WLAN, USB-attached internal NICs,
# the hotspot, IPv6 tunnels, container veths). Hidden unless they carry a usable LAN address.
_NOISE_NAME = re.compile(r"^(awdl|llw|anpi|ap|gif|stf|XHC|p2p)\d+$|^veth", re.I)


def _mac(raw: str | None) -> str | None:
    """A MAC as lower-case colon pairs, or None for a missing or all-zero one."""
    if not raw:
        return None
    parts = re.split(r"[:\-]", raw.strip().lower())
    if len(parts) != 6 or not all(re.fullmatch(r"[0-9a-f]{1,2}", p) for p in parts):
        return None
    mac = ":".join(p.zfill(2) for p in parts)
    return None if mac == "00:00:00:00:00:00" else mac


def _guess(name: str) -> tuple[str, bool, bool]:
    """(kind, virtual, vpn) from the name alone."""
    if name.startswith("lo"):
        return "loopback", True, False
    if _VPN_NAME.match(name):
        return "vpn", True, True
    if _VIRTUAL_NAME.match(name):
        return ("bridge" if name.lower().startswith(("bridge", "br-", "virbr", "docker")) else "virtual"), True, False
    return "other", False, False


# --- macOS: ifconfig -v and networksetup ------------------------------------------------------------------

_IFCONFIG = "/sbin/ifconfig"
_NETWORKSETUP = "/usr/sbin/networksetup"


def _run(args: list[str]) -> str:
    """A fixed system command's output, or "" when it cannot run. The arguments never come from a user."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, check=False).stdout  # nosec B603
    except (OSError, subprocess.SubprocessError):
        return ""


def _speed_from_media(media: str) -> int | None:
    """Mbit/s from an ifconfig media line: "autoselect (1000baseT <full-duplex>)" -> 1000, "10Gbase-T" -> 10000."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(G?)base", media, re.I)
    if not m:
        return None
    return int(float(m.group(1)) * (1000 if m.group(2) else 1))


def _parse_ifconfig(text: str) -> dict[str, dict]:
    """`ifconfig -a -v` output, one entry per interface: flags, ether, status, media and type."""
    out: dict[str, dict] = {}
    cur: dict | None = None
    for line in text.splitlines():
        head = re.match(r"^(\S+?): flags=\w+<([^>]*)>", line)
        if head:
            cur = out.setdefault(head.group(1), {"flags": set(filter(None, head.group(2).split(",")))})
            continue
        if cur is None:
            continue
        m = re.match(r"^\s+(ether|status|media|type):?\s+(.+?)\s*$", line)
        if m and m.group(1) not in cur:
            cur[m.group(1)] = m.group(2)
    return out


def _parse_networksetup(text: str) -> dict[str, str]:
    """`networksetup -listallhardwareports` output: device name -> hardware port name ("en5" -> "USB 10/100/1000 LAN")."""
    out: dict[str, str] = {}
    port = None
    for line in text.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port:
            out[line.split(":", 1)[1].strip()] = port
            port = None
    return out


def _mac_links_from(ifconfig: str, networksetup: str) -> list[_Link]:
    """macOS link records from the two commands' output (kept apart so tests can feed canned text)."""
    hardware = _parse_networksetup(networksetup)
    links: list[_Link] = []
    for name, info in _parse_ifconfig(ifconfig).items():
        flags = info["flags"]
        port = hardware.get(name)
        osk = (info.get("type") or "").lower()
        if "LOOPBACK" in flags:
            kind, virtual, vpn = "loopback", True, False
        elif _VPN_NAME.match(name) or ("POINTOPOINT" in flags and not port):
            kind, virtual, vpn = "vpn", True, True
        elif port and "bridge" in port.lower() or name.startswith("bridge"):
            kind, virtual, vpn = "bridge", True, False
        elif osk == "wi-fi" or (port or "").lower() == "wi-fi":
            kind, virtual, vpn = "wifi", False, False
        elif port or "ethernet" in osk:
            kind, virtual, vpn = "ethernet", False, False
        else:
            kind, virtual, vpn = "virtual", True, False
        status = (info.get("status") or "").lower()
        up = True if status == "active" else False if status == "inactive" else ("RUNNING" in flags)
        links.append(_Link(key=name, name=name, friendly_name=port or name, mac=_mac(info.get("ether")),
                           up=up, speed_mbps=_speed_from_media(info.get("media") or ""),
                           kind=kind, virtual=virtual, vpn=vpn, loopback=kind == "loopback"))
    return links


_mac_cache: dict = {"signature": None, "at": 0.0, "links": []}


def _mac_links(adapters: list[_Adapter]) -> list[_Link]:
    """macOS link records, rerun only when the adapters' addresses change or the cache is old.

    A cable pulled or plugged in changes the addresses (DHCP, or a 169.254 address when there is none),
    so the 5-second watch notices it at once without starting two processes every poll.
    """
    signature = tuple(sorted((a.key, a.ipv4) for a in adapters))
    now = time.monotonic()
    if signature != _mac_cache["signature"] or now - _mac_cache["at"] > MAC_REFRESH_SECONDS:
        ifconfig = _run([_IFCONFIG, "-a", "-v"])
        if ifconfig:  # keep the last good answer when the command fails
            _mac_cache["links"] = _mac_links_from(ifconfig, _run([_NETWORKSETUP, "-listallhardwareports"]))
        _mac_cache["signature"], _mac_cache["at"] = signature, now
    return list(_mac_cache["links"])


# --- Linux: /sys/class/net ---------------------------------------------------------------------------------

_ARPHRD_LOOPBACK, _ARPHRD_PPP, _ARPHRD_NONE = 772, 512, 65534


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read().strip() or None
    except OSError:  # a port that is down answers EINVAL for its speed
        return None


def _linux_links(root: str = "/sys/class/net") -> list[_Link]:
    """Linux link records from sysfs (root is a parameter so tests can point it at a fake tree)."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    links: list[_Link] = []
    for name in names:
        d = os.path.join(root, name)
        try:
            arphrd = int(_read(os.path.join(d, "type")) or 1)
        except ValueError:
            arphrd = 1
        physical = os.path.exists(os.path.join(d, "device"))
        if arphrd == _ARPHRD_LOOPBACK or name == "lo":
            kind, virtual, vpn = "loopback", True, False
        elif arphrd in (_ARPHRD_PPP, _ARPHRD_NONE) or _VPN_NAME.match(name) or os.path.exists(os.path.join(d, "tun_flags")):
            kind, virtual, vpn = "vpn", True, True
        elif os.path.isdir(os.path.join(d, "bridge")):
            kind, virtual, vpn = "bridge", True, False
        elif os.path.isdir(os.path.join(d, "wireless")) or os.path.exists(os.path.join(d, "phy80211")):
            kind, virtual, vpn = "wifi", False, False
        elif physical:
            kind, virtual, vpn = "ethernet", False, False
        else:
            kind, virtual, vpn = "virtual", True, False
        oper = (_read(os.path.join(d, "operstate")) or "").lower()
        if oper == "up":
            up: bool | None = True
        elif oper in ("down", "lowerlayerdown", "dormant", "notpresent"):
            up = False
        else:  # "unknown" (tun devices, some drivers): fall back to the carrier
            carrier = _read(os.path.join(d, "carrier"))
            up = None if carrier is None else carrier == "1"
        try:
            speed = int(_read(os.path.join(d, "speed")) or 0)
        except ValueError:
            speed = 0
        links.append(_Link(key=name, name=name, friendly_name=name, mac=_mac(_read(os.path.join(d, "address"))),
                           up=up, speed_mbps=speed if speed > 0 else None,
                           kind=kind, virtual=virtual, vpn=vpn, loopback=kind == "loopback"))
    return links


# --- Windows: GetAdaptersAddresses ---------------------------------------------------------------------------

_WIN_VPN = ("vpn", "wireguard", "tap-windows", "tailscale", "openvpn", "zerotier", "fortinet", "anyconnect", "wintun")
_WIN_VIRTUAL = ("virtual", "hyper-v", "vmware", "virtualbox", "loopback", "wan miniport", "teredo", "isatap",
                "6to4", "kernel debug", "pseudo-interface")
_IF_ETHERNET, _IF_PPP, _IF_LOOPBACK, _IF_PROPVIRTUAL, _IF_WIFI, _IF_TUNNEL = 6, 23, 24, 53, 71, 131


def _win_link(adapter_name: str, description: str, friendly: str, mac: bytes, if_type: int,
              oper_status: int, speed_bps: int) -> _Link:
    """One Windows link record from the fields of an IP_ADAPTER_ADDRESSES entry."""
    desc = (description or "").lower()
    if if_type == _IF_LOOPBACK or "loopback" in desc:
        kind, virtual, vpn = "loopback", True, False
    elif if_type == _IF_PPP or any(w in desc for w in _WIN_VPN):
        kind, virtual, vpn = "vpn", True, True
    elif if_type in (_IF_TUNNEL, _IF_PROPVIRTUAL) or any(w in desc for w in _WIN_VIRTUAL):
        kind, virtual, vpn = "virtual", True, False
    elif if_type == _IF_WIFI:
        kind, virtual, vpn = "wifi", False, False
    elif if_type == _IF_ETHERNET:
        kind, virtual, vpn = "ethernet", False, False
    else:
        kind, virtual, vpn = "other", False, False
    up = True if oper_status == 1 else None if oper_status in (3, 4) else False  # 3 testing, 4 unknown
    speed = speed_bps // 1_000_000 if 0 < speed_bps < 2 ** 63 else 0
    return _Link(key=adapter_name, name=friendly or adapter_name, friendly_name=description or friendly or adapter_name,
                 mac=_mac(":".join(f"{b:02x}" for b in mac)) if mac else None, up=up,
                 speed_mbps=speed or None, kind=kind, virtual=virtual, vpn=vpn, loopback=kind == "loopback")


def _windows_links() -> list[_Link]:
    import ctypes
    from ctypes import POINTER, c_char_p, c_int, c_uint64, c_ubyte, c_ulong, c_void_p, c_wchar_p

    class Addresses(ctypes.Structure):  # IP_ADAPTER_ADDRESSES_LH, up to the link speeds
        pass

    Addresses._fields_ = [
        ("Length", c_ulong), ("IfIndex", c_ulong), ("Next", POINTER(Addresses)), ("AdapterName", c_char_p),
        ("FirstUnicastAddress", c_void_p), ("FirstAnycastAddress", c_void_p), ("FirstMulticastAddress", c_void_p),
        ("FirstDnsServerAddress", c_void_p), ("DnsSuffix", c_wchar_p), ("Description", c_wchar_p),
        ("FriendlyName", c_wchar_p), ("PhysicalAddress", c_ubyte * 8), ("PhysicalAddressLength", c_ulong),
        ("Flags", c_ulong), ("Mtu", c_ulong), ("IfType", c_ulong), ("OperStatus", c_int), ("Ipv6IfIndex", c_ulong),
        ("ZoneIndices", c_ulong * 16), ("FirstPrefix", c_void_p), ("TransmitLinkSpeed", c_uint64),
        ("ReceiveLinkSpeed", c_uint64),
    ]
    get = ctypes.windll.iphlpapi.GetAdaptersAddresses  # type: ignore[attr-defined]
    size = c_ulong(16 * 1024)
    for _ in range(4):  # the list can grow between the size query and the call
        buf = ctypes.create_string_buffer(size.value)
        ret = get(0, 0x0E, None, buf, ctypes.byref(size))  # AF_UNSPEC; skip anycast, multicast, DNS servers
        if ret == 0:
            break
        if ret != 111:  # anything but ERROR_BUFFER_OVERFLOW
            return []
    else:
        return []
    links: list[_Link] = []
    p = ctypes.cast(buf, POINTER(Addresses))
    while p:
        a = p.contents
        links.append(_win_link((a.AdapterName or b"").decode(errors="replace"), a.Description or "",
                               a.FriendlyName or "", bytes(a.PhysicalAddress[: min(a.PhysicalAddressLength, 8)]),
                               a.IfType, a.OperStatus, a.TransmitLinkSpeed))
        p = a.Next
    return links


# --- ports ---------------------------------------------------------------------------------------------------

def _links(adapters: list[_Adapter]) -> list[_Link]:
    """The OS's link records, or none on a platform (or failure) where they cannot be read."""
    try:
        if sys.platform == "darwin":
            return _mac_links(adapters)
        if sys.platform.startswith("linux"):
            return _linux_links()
        if sys.platform == "win32":
            return _windows_links()
    except Exception:
        log.exception("could not read network link state")
    return []


def _port(key: str, nice: str, ipv4: tuple[tuple[str, int], ...], link: _Link | None, default_ip: str | None) -> Port:
    if link:
        name, friendly = link.name, link.friendly_name
        kind, virtual, vpn = link.kind, link.virtual, link.vpn
    else:
        name, friendly = key, nice or key
        kind, virtual, vpn = _guess(key)
    ips = [ip for ip, _ in ipv4]
    loopback = kind == "loopback" or any(ipaddress.ip_address(ip).is_loopback for ip in ips)
    if loopback:
        kind, virtual, vpn = "loopback", True, False
    return Port(
        id="", name=name, friendly_name=friendly, mac=link.mac if link else None,
        ipv4=tuple(f"{ip}/{prefix}" for ip, prefix in ipv4),
        default_route=bool(default_ip) and default_ip in ips,
        up=link.up if link else None, speed_mbps=link.speed_mbps if link else None,
        kind=kind, virtual=virtual, vpn=vpn, loopback=loopback,
        link_local_only=bool(ips) and all(ipaddress.ip_address(ip).is_link_local for ip in ips),
    )


def _with_ids(ports: list[Port]) -> list[Port]:
    """Give each port its stable id: mac:<mac> for hardware with a MAC, name:<name> otherwise.

    A bridge or VPN is keyed by name because it borrows a member's MAC (macOS's Thunderbolt Bridge has en1's)
    or has none. Two hardware ports with the same MAC (rare) each add their name so neither id is shared.
    """
    base = [f"mac:{p.mac}" if p.mac and not p.virtual else f"name:{p.name}" for p in ports]
    out = []
    for p, b in zip(ports, base, strict=True):
        pid = f"{b}/{p.name}" if b.startswith("mac:") and base.count(b) > 1 else b
        out.append(Port(**{**asdict(p), "id": pid, "ipv4": p.ipv4}))
    return out


def _hidden(p: Port) -> bool:
    """Ports left out of interfaces(): loopback and the OS's own plumbing, unless it carries a LAN address."""
    if p.loopback:
        return True
    if any(_usable(ip) for ip in p.addresses):
        return False  # lan_ips() lists it, so the port list must too
    if _NOISE_NAME.match(p.name):
        return True
    return p.virtual and not p.ipv4  # an idle tunnel or bridge with nothing on it


def interfaces(include_hidden: bool = False) -> list[Port]:
    """One record per usable network port, those with addresses first, in the order the OS lists them.

    Hardware ports with no address (a cable pulled, an adapter with nothing plugged in) are listed, marked
    down. `include_hidden` adds loopback and the OS's idle internal interfaces.
    """
    adapters = _adapters()
    links = {link.key: link for link in _links(adapters)}
    default_ip = _default_route_ip()
    ports: list[Port] = []
    seen: set[str] = set()
    for a in adapters:
        seen.add(a.key)
        ports.append(_port(a.key, a.nice_name, a.ipv4, links.get(a.key), default_ip))
    for key, link in links.items():
        if key not in seen:
            ports.append(_port(key, link.friendly_name, (), link, default_ip))
    ports = _with_ids(ports)
    return [p for p in ports if include_hidden or not _hidden(p)]


# --- watching for changes --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Change:
    """What watch() saw change: the new address list and port list, and how the ports differ from before."""
    ips: list[str]
    ports: list[Port]
    ips_changed: bool
    added: list[Port] = field(default_factory=list)
    removed: list[Port] = field(default_factory=list)
    changed: list[tuple[Port, Port]] = field(default_factory=list)  # (before, after), matched by id


def diff_ports(before: list[Port], after: list[Port]) -> tuple[list[Port], list[Port], list[tuple[Port, Port]]]:
    """(added, removed, changed) between two port lists, matched by stable id, so a rename is a change."""
    old = {p.id: p for p in before}
    new = {p.id: p for p in after}
    added = [p for i, p in new.items() if i not in old]
    removed = [p for i, p in old.items() if i not in new]
    changed = [(old[i], p) for i, p in new.items() if i in old and old[i] != p]
    return added, removed, changed


def _describe(before: Port, after: Port) -> str:
    parts = []
    if before.name != after.name:
        parts.append(f"renamed {before.name} -> {after.name}")
    if before.up != after.up:
        parts.append({True: "link up", False: "link down", None: "link state unknown"}[after.up])
    if before.ipv4 != after.ipv4:
        parts.append(f"addresses {', '.join(before.ipv4) or 'none'} -> {', '.join(after.ipv4) or 'none'}")
    if before.speed_mbps != after.speed_mbps:
        parts.append(f"speed {before.speed_mbps} -> {after.speed_mbps} Mbit/s")
    if before.default_route != after.default_route:
        parts.append("now carries the default route" if after.default_route else "no longer the default route")
    return "; ".join(parts) or "changed"


def _read_network() -> tuple[list[str], list[Port]]:
    return lan_ips(), interfaces()


async def watch(on_change: Callable[[Change], None], interval: float = POLL_SECONDS) -> None:
    """Call `on_change(change)` whenever the LAN addresses change or a port appears, goes or changes."""
    last_ips, last_ports = await asyncio.to_thread(_read_network)
    while True:
        await asyncio.sleep(interval)
        try:
            ips, ports = await asyncio.to_thread(_read_network)
        except Exception:
            log.exception("could not read network interfaces")
            continue
        added, removed, changed = diff_ports(last_ports, ports)
        ips_changed = ips != last_ips
        if not (ips_changed or added or removed or changed):
            continue
        if ips_changed:
            log.info("LAN addresses changed: %s -> %s", last_ips or "none", ips or "none")
        for p in added:
            log.info("Network port added: %s (%s) %s", p.name, p.friendly_name, ", ".join(p.ipv4) or "no address")
        for p in removed:
            log.info("Network port removed: %s (%s)", p.name, p.friendly_name)
        for before, after in changed:
            log.info("Network port %s: %s", after.name, _describe(before, after))
        last_ips, last_ports = ips, ports
        try:
            on_change(Change(ips, ports, ips_changed, added, removed, changed))
        except Exception:
            log.exception("LAN change handler failed")
