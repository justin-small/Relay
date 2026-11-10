---
title: "Touch-screen caption panels: scope chosen for the Elecrow CrowPanel 7-inch"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 3, tokens: 34595 }
id: l-44e463b1
scope: repo
status: active
replaces: l-b4a2c356
src: agent
touches:
  - app/main.py
  - app/hub.py
  - app/config.py
---

## Decided
Caption panels on the Elecrow CrowPanel Advance 7" (same board as VXP), firmware in this repo copied from VXP's compact display. They show finished lines only, never word-by-word, for transcription and translations alike. Latin script only, so non-Latin languages are hidden on panels. Feed is set from admin or at the screen, newest wins. One or two feeds; follow live with Jump to live; 200 lines kept. Plain HTTP on the LAN, mDNS discovery, admin can list panels, set feed and push signed firmware. Tracked in the GitHub epic justin-small/Relay#76 (sub-issues #64-#75).

## Watch out
- Captions need no new API beyond a finished-lines-only option on /stream; the new API is the panel registry, feed control and firmware.
- Relay's history is configurable up to 500 lines, so set it to 200 for a rebooted panel to refill.
- mDNS from inside Docker Desktop doesn't reach the LAN; announce from the host.
- Firmware over plain HTTP is safe only because the panel checks the image signature.

In the code: GET /stream with ?last_id= (app/main.py, app/hub.py); history_lines in app/config.py; Caddy refuses /admin and /api/admin on the viewer port (docker-config/Caddyfile).
