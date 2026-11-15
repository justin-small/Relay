---
title: Intercom downloads LiveKit once on Linux and Windows; on a Mac the operator supplies the binary
author: Justin
date: 2026-11-15
branch: intercom-data-and-livekit-237
cost: { minutes: 10, tokens: 150354 }
id: l-2415fd6e
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/__init__.py
  - app/modules/intercom/livekit.py
  - app/modules/intercom/migrations/0001_intercom.sql
  - app/modules/intercom/routes.py
  - app/modules/intercom/static/intercom.js
  - app/modules/intercom/store.py
  - app/modules/intercom/templates/admin.html
  - docs/modules.md
  - tests/test_auth.py
  - tests/test_intercom.py
---

## Decided
Justin chose (2026-11-14, issue 237, slice #272 + #238): Relay downloads the pinned livekit-server release itself on first switch-on (SHA-256 checked, kept in the state folder, never fetched again), and LiveKit publishes no macOS build, so on a Mac the operator installs it (brew install livekit) and enters its path in the module's settings. The first slice was the data model plus the supervised server only.

## Tried and ruled out
- Relay builds and hosts its own macOS binaries in release CI: more work and a second release pipeline; offered, not chosen.
- Linux and Windows only: would leave Mac desktop installs without intercom.
- Bundling binaries in the desktop app: not chosen; download-once was.

## Watch out
- The WebSocket proxy at /intercom/rtc and LiveKit's webhook are not built yet (sign-in rules belong with #239 and #273).
- Never run against a real livekit-server, only a fake script.
- The API secret sits in a 0600 file until #278 encrypts it.
- Tests that switch modules on must set RELAY_LIVEKIT_NO_DOWNLOAD=1 or Linux CI would fetch LiveKit.

In the code: app/modules/intercom/livekit.py (ASSETS, download, Supervisor), docs/modules.md rule 6.
