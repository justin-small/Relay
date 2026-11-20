---
title: "To check a module page in the UI demo panel while the installed Relay app is running, use a scratch demo state with Crew off, and restart the demo after editin…"
author: Justin
date: 2026-11-18
branch: ndi-redesign
id: l-09362035
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/__init__.py
---

## Decided
2026-11-18, checking the NDI page: the UI demo panel (docs/ui-audit/demo/serve.py, plain HTTP on 127.0.0.1:18181, owner / demo pass 2026) is the quickest way to see a module page in the real operator panel, and with cyndilib installed it finds real NDI sources on the network.

## Tried and ruled out
- Starting it with the default state while the installed Relay desktop app runs: it exits, because the Crew module's join page binds fixed port 8095, which the installed app already holds.
- Starting it from the preview pane (preview_start): it reported the port as in use even when it was free, and left a half-started process holding the fake device ports.

## Watch out
- Script and stylesheet links carry a ?v= version fixed when the server starts, so after editing a module's static files, restart the demo; a page reload alone keeps the old script.
- After a Ctrl-C or SIGTERM the demo can leave its fake ProPresenter/OBS servers on ports 18201-18203; kill it with -9 before starting again.

In the code: RELAY_UI_DEMO_STATE=<scratch dir> .venv/bin/python docs/ui-audit/demo/serve.py once to seed, set modules.crew.enabled false in <scratch dir>/config.json, run it again. Crew's port is JOIN_PORT in app/modules/crew/__init__.py.
