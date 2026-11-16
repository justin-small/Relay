---
title: "Every module's outside traffic goes through a routed helper with its own traffic class, and a contract test refuses a module that opens a socket itself"
author: Justin
date: 2026-11-16
branch: network-module-traffic
cost: { minutes: 11, tokens: 255153 }
id: l-9f3b1b54
scope: repo
status: active
src: agent
touches:
  - tests/test_modules.py
  - tests/test_module_traffic.py
  - docs/modules.md
  - app/netsock.py
---

## Decided
Issue 348 (2026-11-15), built without asking Justin: one class per module (atem, propresenter, tally_arbiter, planning_center, remote_access, intercom as media/strict, crew.push, signage_web, alert_events) and core's translation for OpenAI. Internet classes fall back to the default route; only intercom's LiveKit media is strict.
RouteError is now an OSError, so existing `except OSError` around a connection keeps working. Unrouted TCP uses socket.create_connection, exactly as before (IPv6 too).
Pages and connection tests show netsock.failure(class, default sentence), built from the route, never str(exc) (CodeQL).
Core health now also names a routed class that fell back to the default network, not only a strict one that cannot run.
cloudflared is steered with --edge-bind-address; LiveKit with rtc.node_ip plus rtc.interfaces/ips includes.

## Tried and ruled out
`async with await websockets.connect(sock=...)`: websockets 13.1's legacy protocol object has no __aenter__; use netsock.websocket_session.
Passing your own HTTPSHandler to netsock.opener: build_opener would let it replace the routed one; opener() refuses it, pass context=.

## Watch out
Not run on real hardware: a second NIC, LiveKit's interface filter, and cloudflared's --edge-bind-address are untested. Not steered: the remote intercom bridge's aiortc media, phones' LiveKit signalling (crew port), LiveKit's one-time download.
Dante, AES67 and NDI modules do not exist yet; docs/modules.md says how they must use this.

In the code: app/netsock.py (http_connection, opener, urlopen, websocket, websocket_session, udp_endpoint remote=, multicast_sender, failure), netroute.health_problem, MODULE_SOCKETS and opened() in tests/test_modules.py, tests/test_module_traffic.py, docs/modules.md "Network traffic on a port of its own".
