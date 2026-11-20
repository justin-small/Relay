---
title: "On Windows a UDP endpoint made from a handed-in socket must be told its peer, and a refused connection takes about two seconds to report"
author: Justin
date: 2026-11-16
branch: lighting-module
cost: { minutes: 2, tokens: 19909 }
id: l-3e84f991
scope: repo
status: active
src: agent
touches:
  - app/netsock.py
  - tests/test_netsock.py
  - app/modules/__init__.py
  - app/modules/lighting/__init__.py
  - app/modules/lighting/backend.py
  - app/modules/lighting/routes.py
  - app/modules/lighting/service.py
  - app/modules/lighting/static/lighting.js
  - app/modules/lighting/templates/lighting.html
  - docs/modules.md
---

## Decided
Windows CI had failed on main since the network-port work (#410), not because of the lighting PR. asyncio's Windows transport does not learn the peer of a socket it is given, so sendto(data) with no address fails inside WSASendTo. The routed UDP endpoint now sets the peer itself on Windows, so callers keep sending without an address. The closed-port test waits 10 seconds, since Windows retries a refused connection for about two seconds before reporting it.

## Tried and ruled out
Wrapping the transport so sendto adds the address: macOS refuses sendto with an address on a connected UDP socket, and protocols holding their own transport would still fail.

## Watch out
The fix sets a private asyncio field (_address on the proactor datagram transport); re-check it when the Python version changes. Not run on a Windows machine, only in CI. The CodeQL check fails on a PR with no Rust change because the Rust analysis is skipped and its configuration counts as missing.

In the code: udp_endpoint in app/netsock.py, tests/test_netsock.py (closed port), tests/test_atem.py and tests/test_module_traffic.py (UDP on Windows).
