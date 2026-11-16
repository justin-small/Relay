---
title: "Routed sockets fall back to the source address when Linux refuses the interface pin, and unrouted listeners never bind every interface"
author: Justin
date: 2026-11-16
branch: network-sockets
cost: { minutes: 8, tokens: 66727 }
id: l-f0d1f100
scope: repo
status: active
src: agent
touches:
  - app/config.py
  - app/main.py
  - app/modules/__init__.py
  - app/netroute.py
  - docs/modules.md
  - tests/test_modules.py
  - tests/test_netroute.py
  - app/netsock.py
  - tests/test_netsock.py
  - app/netinfo.py
---

## Decided
Issue 345, 2026-11-15, Justin's choices: when Linux refuses SO_BINDTODEVICE (no CAP_NET_RAW), the helper keeps the port's source address and records a note (netsock.notes) instead of failing, since that is right for devices on the port's own subnet. Helpers return configured sockets plus async wrappers (open_tcp, start_server, udp_endpoint).
An unrouted listener binds the caller's host (a setting) or the default network's address, never "" or 0.0.0.0, because CodeQL blocks new all-interface binds. Windows multicast binds the port's own address, since Windows cannot bind a group.

## Watch out
macOS IP_BOUND_IF (25) and Windows IP_UNICAST_IF (31, index packed big-endian) are not in Python's socket module; they are constants in netsock.py. Port.index now comes from ifaddr; macOS falls back to if_nametoindex.
The real loopback test proves the source pin only on Linux (127.0.0.2 and .3 are on lo); macOS has only 127.0.0.1, Windows runs the unrouted path.

In the code: app/netsock.py, Port.index in app/netinfo.py, tests/test_netsock.py, docs/modules.md.
