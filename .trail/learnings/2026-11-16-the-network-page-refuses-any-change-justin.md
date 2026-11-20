---
title: "The Network page refuses any change that would take the panel off the address the admin is using, and Test probes without sending"
author: Justin
date: 2026-11-16
branch: network-page
cost: { minutes: 11, tokens: 157391 }
id: l-995ab407
scope: repo
status: active
src: agent
touches:
  - app/announce.py
  - app/main.py
  - app/modules/__init__.py
  - app/modules/crew/__init__.py
  - app/modules/crew/routes.py
  - app/modules/signage/routes.py
  - app/netroute.py
  - app/serving.py
  - app/tls.py
  - docs/modules.md
---

## Decided
Issue 347, 2026-11-15, Justin's choices: network.view is viewer and up (like settings and modules), network.manage admin. A crew route change that would leave the operator panel without the address the browser connected to (request.scope["server"]) is refused outright with a 409; it can be made from 127.0.0.1 or from a browser already on the new network. Test opens a pinned UDP socket through the route and connect()s it to a neighbour on that port's subnet (TEST-NET-1 for an unrouted class): no packet is sent, and getsockname() shows the source the OS chose. A module with its own connection test also reruns it.
A port that is connected but not yet in known_ports can be chosen; choosing remembers it. Ports that are gone and carry no route can be forgotten.

## Watch out
The page re-reads every 5 s and redraws, so Test results are kept per class in the script, and a redraw is skipped while a dropdown has focus.
Static files are versioned by a hash computed when the server starts: after editing network.js, restart a test server, and reload the page (navigating to the same URL with only a #fragment does not reload).
Screenshots for the PR are in docs/guide/images/admin-network-*.png, taken from a scratch server with fake ports over plain HTTP.

In the code: netroute.overview/change/forget, netsock.probe, /api/admin/network* in app/main.py, app/static/network.js, the page in app/templates/admin.html, tests/test_network_page.py.
