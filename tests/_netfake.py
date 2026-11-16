"""Fake network ports for tests (#349), so nothing needs a second network card.

Import it after the test has set RELAY_CONFIG (it imports the app). Then:

    from _netfake import WIFI, USB, DANTE_NET, port, use_ports, lan, route, policy, loopback_route

    use_ports(WIFI, USB)                 # these are the machine's ports now (netroute's cached list)
    remember(WIFI, USB)                  # and Relay has seen them (config "known_ports"), so routes to them keep
    route(dante=USB.id)                  # exactly these routes, every policy at its default
    with lan("192.168.1.20"):            # the default network's addresses (netinfo.lan_ips)
        netroute.resolve("dante")
    sock = netsock.connect_tcp(loopback_route(), "127.0.0.1", port)   # a real pinned socket over loopback

Ports are made with `port()`: ids come from the last MAC byte, so two tests' ports with the same `mac` are the
same port to Relay.
"""
from __future__ import annotations

import socket
import sys
from unittest import mock

from app import config, netinfo, netroute
from app.netinfo import Port
from app.netroute import Resolved


def port(name: str, ipv4: str | tuple[str, ...] = (), friendly: str = "", *, mac: str, **kw) -> Port:
    """A port: `port("en5", "10.0.0.5/24", "USB LAN", mac="05", index=7)`. Up and wired unless told otherwise."""
    addrs = (ipv4,) if isinstance(ipv4, str) else tuple(ipv4)
    kw.setdefault("up", True)
    kw.setdefault("kind", "ethernet")
    return Port(id=f"mac:aa:00:00:00:00:{mac}", name=name, friendly_name=friendly or name, ipv4=addrs, **kw)


# The usual three: the venue Wi-Fi (default route), a USB adapter on a second LAN, and a Dante network with only a
# self-assigned address.
WIFI = port("en0", "192.168.1.20/24", "Wi-Fi", mac="01", default_route=True, kind="wifi", index=4)
USB = port("en5", "10.0.0.5/24", "USB LAN", mac="05", index=7)
DANTE_NET = port("en6", "169.254.20.7/16", "Thunderbolt Ethernet", mac="06", link_local_only=True, index=6)


def use_ports(*ports: Port) -> None:
    """Make these the machine's ports, as netinfo.watch would."""
    netroute.update(list(ports))


def remember(*ports: Port) -> None:
    """Relay has seen these ports (config known_ports), so a route to one is kept even while it is unplugged."""
    netroute.remember(list(ports))


def lan(*ips: str):
    """A context manager: the default network's addresses are `ips` (none: this computer is offline)."""
    return mock.patch.object(netinfo, "lan_ips", lambda: list(ips))


def route(**routes: str) -> None:
    """Set exactly these routes (class id -> port id; use `**{"crew.push": USB.id}` for a dotted id), with every
    policy at its default. The ports must have been remembered."""
    net = config.get()["network"]
    config.save({"network": {**net, "routes": routes, "policy": {}}}, replace=("network",))


def policy(**policies: str) -> None:
    net = config.get()["network"]
    config.save({"network": {**net, "policy": policies}}, replace=("network",))


def loopback_route(address: str = "127.0.0.1", traffic: str = "test") -> Resolved:
    """A class routed to the loopback port: real pinned sockets without a second network card. On Windows, which
    cannot pin to loopback, the unrouted path (still really sending)."""
    if sys.platform == "win32":
        return Resolved(traffic, None, None, "127.0.0.1", True, "", "fallback")
    name = "lo" if sys.platform.startswith("linux") else "lo0"
    lo = Port(id=f"name:{name}", name=name, friendly_name="Loopback", ipv4=(f"{address}/8",), up=True,
              index=socket.if_nametoindex(name))
    return Resolved(traffic, lo.id, lo, address, True, "", "strict")
