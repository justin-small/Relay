---
title: "The NDI viewer was proved against a real Scan Converter source, after Justin agreed to install cyndilib"
author: Justin
date: 2026-11-17
branch: ndi-viewer-signage-widgets
cost: { minutes: 21, tokens: 54958 }
id: l-ad34ecb0
scope: repo
status: active
src: agent
touches:
  - requirements.txt
  - requirements-release.txt
  - README.md
  - app/boards.py
  - app/modules/ndi/__init__.py
  - app/modules/ndi/routes.py
  - app/modules/ndi/runtime.py
  - app/modules/ndi/service.py
  - app/modules/ndi/static/ndi.js
  - app/modules/ndi/static/view.js
---

## Decided
Justin (2026-11-17) said yes to installing cyndilib into the project venv so the viewer could be tried on real NDI. mDNS discovery, the SDK finder, the receiver and the MJPEG route all worked against another computer's Scan Converter source.
In the code: `.venv/bin/pip install cyndilib`; a scratch Relay (own RELAY_CONFIG, ports 8030/8031) with the ndi module on; stream fetched with curl -k and a throwaway owner made through `users.create`.

## Tried and ruled out
Not installing it and only reporting discovery: offered, not chosen. The built-in browser pane cannot open the operator port (self-signed certificate), so the page was checked with curl, not by eye.

## Watch out
Only about 5 to 8 JPEG frames a second arrived at a 15 fps cap: the numpy UYVY conversion is the likely limit. The audio meter read -120 dB (silence), so it is still unproved with real sound. A Mac running a sender was not advertised over mDNS at all. The local venv is Python 3.10 and cannot install Pillow 13.
