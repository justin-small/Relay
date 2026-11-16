---
title: "Relay's listeners bind their routed port plus loopback, and move live when that port's address changes"
author: Justin
date: 2026-11-16
branch: network-serving
cost: { minutes: 9, tokens: 102659 }
id: l-57614e62
scope: repo
status: active
src: agent
touches:
  - tests/test_net_serving.py
  - docs/modules.md
  - app/serving.py
  - run.py
---

## Decided
Issue 346, 2026-11-15, Justin's choices: a listener whose class is routed binds that port's address and 127.0.0.1 (every class keeps loopback, so the desktop app always reaches it); unrouted or fallen-back classes bind the configured host as before. When the routed port's address changes, or the port goes and comes back, the running server reopens just those sockets (serving.rebind) instead of asking for a restart. A module Listener names its traffic class (default "viewers"; the crew join page uses "crew").
The operator panel locked to a port always shows a recovery notice naming https://127.0.0.1 and the config.json key to remove.

## Watch out
rebind closes old sockets before opening new ones, because Linux refuses a port's specific address while the same port is bound to every address. If nothing can be opened it keeps 127.0.0.1.
uvicorn is started with no sockets of its own; RelayServer.startup serves every group itself and keeps a handle (serving._live) for rebind, which must run on the server's loop (rebind_soon from other threads).
The certificate is now checked on every port change, not only LAN address changes: a crew port on a link-local address is not in lan_ips().
The real per-address server test runs only on Linux (127.0.0.2/.3/.4 on lo); macOS has no extra loopback addresses without sudo.

In the code: plan, listen_group, rebind and make_server in app/serving.py; run.py; netroute.link_addresses; tls._https_addresses; announce.lan_ip and its goodbye/leave on change; tests/test_net_serving.py.
