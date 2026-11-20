---
title: "Network ports are keyed by MAC except bridges and VPNs, hide the OS's own interfaces, and macOS link state is cached behind an address signature"
author: Justin
date: 2026-11-16
branch: network-ports-inventory
cost: { minutes: 10, tokens: 132574 }
id: l-f021e1dc
scope: repo
status: active
src: agent
touches:
  - app/netinfo.py
  - app/main.py
  - tests/test_netinfo.py
---

## Decided
Issue 343, 2026-11-15, Justin's choices: watch() hands one Change object (ips, ports, ips_changed, added/removed/changed) to a single callback rather than adding a second callback; interfaces() hides loopback and the OS's own idle interfaces (awdl, llw, anpi, ap, gif, stf, veth, idle utun/bridges) unless they carry a LAN address, with include_hidden=True for everything; macOS link state comes from `ifconfig -a -v` and `networksetup -listallhardwareports` as subprocesses (bandit nosec), not ctypes ioctls.
A port's id is mac:<mac> for hardware, name:<name> for bridges, VPNs and anything virtual: macOS's Thunderbolt Bridge borrows en1's MAC, and Docker/VPN interfaces have random or no MAC.
lan_ips() reads only ifaddr, never the link-state commands, because it is called on request paths.

## Watch out
ifaddr lists only adapters that have an address, so an unplugged port exists only in the OS records; interfaces() appends those after the addressed ones.
The macOS commands rerun only when the ifaddr address signature changes or every 30 s: a pulled cable or a plug-in changes addresses (DHCP or 169.254) so the 5 s watch still sees it quickly, but a link coming up with no address change can take up to 30 s.
Windows link state uses a hand-written IP_ADAPTER_ADDRESSES_LH struct up to TransmitLinkSpeed (offset 184 on x64); it is only exercised by the real-machine section of the test on Windows CI.
The ifconfig "type:" line must be matched with a space after the colon: agent lines contain "type:NetIf".

In the code: app/netinfo.py (interfaces, _with_ids, _hidden, _mac_links, _linux_links, _windows_links, watch, Change), app/main.py _on_lan_change, tests/test_netinfo.py.
