---
title: "OBS live test findings: scene order, idle means standing by, and the operator panel cannot be opened in the browser pane"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 12, tokens: 214053 }
id: l-3fe72161
scope: repo
status: active
src: agent
touches:
  - app/modules/obs/client.py
  - app/modules/obs/service.py
  - app/modules/obs/__init__.py
  - app/modules/obs/routes.py
  - app/modules/obs/static/obs.js
  - tests/test_obs.py
  - app/static/status.js
  - firmware/relay-display/main/status_format.h
---

## Decided
- Issue 250, 2026-11-16: the status view's word for health "idle" changed from "not connected" to "standing by" (web status.js and the panel's status_format.h), because the OBS module reports idle when OBS is connected but not streaming. Unreachable is "error".

## Tried and ruled out
- Sorting OBS scenes by sceneIndex ascending: wrong. obs-websocket 5 gives the top scene the highest sceneIndex, so sort descending to match OBS's list.

## Watch out
- Tested live against OBS 33.0.1 / obs-websocket 5.7.6 on Justin's Mac (port 4455, no auth): connect, scenes, switching both ways, recording health, JPEG preview, the /screen scene page and the status view all worked. Streaming, dropped frames and SendStreamCaption were tested only against the fake server in tests/test_obs.py: there was no stream target.
- OBS's CreateScene inserts the new scene under the selected one, and RemoveScene takes effect a moment later, so a GetSceneList straight after can still list it.
- The operator panel is HTTPS with a self-signed certificate, so the in-app browser pane refuses it; check the admin page in a browser that trusts Relay's certificate, or through the tests.

In the code: app/modules/obs/service.py (_read_scenes sort), app/static/status.js HEALTH, firmware/relay-display/main/status_format.h status_health_words.
