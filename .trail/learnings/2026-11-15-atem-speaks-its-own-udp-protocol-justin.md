---
title: "ATEM speaks its own UDP protocol in the module, and Tally Arbiter is followed over a hand-rolled Socket.IO client"
author: Justin
date: 2026-11-15
branch: cameras-tally-242
id: l-048b72e1
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/main.py
  - app/static/viewer.css
  - tests/test_auth.py
  - tests/test_modules.py
---

## Decided
Issue 242 (Justin, 2026-11-14): build #243-#245 and #248; NDI (#247) and the panel firmware (#246) left out. One branch, one PR.
The ATEM module implements the ATEM UDP protocol itself (asyncio), as #243 said, from atem-connection's source (the library Bitfocus Companion uses). Tally Arbiter is followed with a minimal Socket.IO client over the `websockets` package already in requirements, joining each device as a listener client (no login needed).
The shared tally state is core (`app/tally.py`, in CORE_FILES), the screens read only that. USB camera tiles are browser-side getUserMedia in core's tally.js, not a module: nothing leaves the computer.

## Tried and ruled out
PyATEMMax (GPL-3, beta) and pyatem (LGPL-3): both are sdist-only with no wheel, thread-based not asyncio, and the desktop release installs hash-pinned wheels.
Tally Arbiter's REST/webhooks: it has no REST tally endpoint; Socket.IO is its listener API. Its 'producer' and 'companion' rooms need a login, so a listener-client join per device is used.

## Watch out
Not run against a real switcher or a real Tally Arbiter: only against fakes in tests/test_atem.py and tests/test_tally_arbiter.py that speak the same framing. The ATEM downstream-key auto command differs before protocol 2.29 (version-checked). getUserMedia works only on https or localhost, so USB tiles are not offered on plain-HTTP viewer addresses.
In the code: app/modules/atem/protocol.py, client.py; app/modules/tally_arbiter/listener.py; app/tally.py; app/static/tally.js.
