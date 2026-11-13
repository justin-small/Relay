---
title: Relay announces itself to caption panels from inside the app on launch; Docker is gone
author: Justin
date: 2026-11-13
branch: feature/state-folder-180
cost: { minutes: 2, tokens: 26902 }
id: l-0c206397
scope: repo
status: active
replaces: l-c621bb37
src: agent
touches:
  - README.md
  - app/announce.py
  - app/main.py
  - tests/test_announce.py
  - tests/test_auth.py
  - tests/test_modules.py
  - tools/setup_caddy.py
  - tools/announce.ps1
  - tools/migrate-from-docker.ps1
---

## Decided
Docker was removed (#180); the panel announcement (mDNS) now lives in the app (app/announce.py, an Announcer thread started in _startup and stopped in _shutdown), so nothing external starts it. RELAY_ANNOUNCE=0 turns it off, RELAY_INSTALL_NAME names the install. It is core, not a module: it announces Relay to its own panels, not another product. tools/setup_caddy.py is kept for the HTTPS work; tools/announce.ps1 is kept as a Windows fallback.

## Tried and ruled out
Leaving it a hand-run tool: the launchers that started it are gone, so panels would silently stop finding Relay.

## Watch out
Tests that run the app's lifespan must set RELAY_ANNOUNCE=0 (test_auth does), or they multicast from the test run. A failure to bind UDP 5353 is logged and ignored. Windows may have its own mDNS responder holding 5353; the Python announcer is untested there. The Windows migration script was only run under PowerShell on macOS.

In the code: app/announce.py, app/main.py (_startup, _shutdown), tests/test_announce.py, tests/test_auth.py, tools/migrate-from-docker.ps1.
