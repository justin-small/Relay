"""This machine's LAN addresses, worked out from the network interfaces.

Viewer links, the QR codes and (later) the certificate all need the address a
phone on the same network would dial. Run natively there is no launcher to hand
it in, so read it from the interfaces, and notice when it changes (a laptop
moving from one Wi-Fi network to another).

`RELAY_ADMIN_IPS` is kept as optional extras: addresses the interfaces cannot
show, such as a port forward or a VPN name the operator wants on the page.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from collections.abc import Callable

log = logging.getLogger("relay.netinfo")

POLL_SECONDS = 5.0


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


def _interface_ips() -> list[str]:
    """Every usable IPv4 address on the machine's interfaces."""
    try:
        import ifaddr
    except ImportError:  # the app still serves; it falls back to the default route
        return []
    out: list[str] = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            try:
                addr = ipaddress.ip_address(ip.ip)
            except ValueError:
                continue
            if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
                continue
            if str(addr) not in out:
                out.append(str(addr))
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


async def watch(on_change: Callable[[list[str]], None], interval: float = POLL_SECONDS) -> None:
    """Call `on_change(new_addresses)` whenever the set of LAN addresses changes."""
    last = await asyncio.to_thread(lan_ips)
    while True:
        await asyncio.sleep(interval)
        try:
            now = await asyncio.to_thread(lan_ips)
        except Exception:
            log.exception("could not read network interfaces")
            continue
        if now != last:
            log.info("LAN addresses changed: %s -> %s", last or "none", now or "none")
            last = now
            try:
                on_change(now)
            except Exception:
                log.exception("LAN change handler failed")
