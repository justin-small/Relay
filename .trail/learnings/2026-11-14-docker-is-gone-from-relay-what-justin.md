---
title: Docker is gone from Relay; what still mentions it on purpose and how an installed app recovers a lockout
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 2895 }
id: l-de86c173
scope: repo
status: active
replaces: l-c621bb37
src: agent
touches:
  - README.md
  - spike/entry.py
  - docs/guide/src/16-upgrading.md
  - app/importdocker.py
---

## Decided
Issue 187 finished the Docker removal: README and guide describe the desktop installers (and the headless service), the tracked docker-config/ folder (it had been committed by mistake in #193, panel cert key included) is untracked, and docs/guide/build.sh needs pandoc and typst installed instead of falling back to a container. Docker words left on purpose: the v2.0 import (app/importdocker.py, tools/import_docker.py, the first-run page, the Upgrading chapter, troubleshooting about an old container panels find), the firmware build (Espressif's ESP-IDF container), the Companion screenshot tool (Companion's own dev compose) and a crate name in Cargo.lock.

## Tried and ruled out
Telling desktop users to run tools/reset_owner.py: an installed app has no Python. The frozen backend now takes `relay --reset-owner <username>` (spike/entry.py runs the same tool), and release.yml checks it is bundled (no username: exit 2).

## Watch out
- Not run on real machines: the reset command's paths in README/guide (macOS Relay.app/Contents/Resources/relay-backend/relay, Windows %LOCALAPPDATA%\Relay\relay-backend\relay.exe, the .deb's /usr/lib/Relay), rehearsal mode via RELAY_DEMO=1 relay-desktop, and the Gatekeeper/SmartScreen steps.
- Rehearsal mode still has no switch in the app: quit, then start relay-desktop from a terminal with RELAY_DEMO=1.

In the code: spike/entry.py (reset_owner), README.md (Install, Upgrading from v2.0, If nobody can sign in), docs/guide/src/1*.md.
