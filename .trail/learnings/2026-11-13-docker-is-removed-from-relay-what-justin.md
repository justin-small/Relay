---
title: Docker is removed from Relay; what was kept on purpose and what nothing starts any more
author: Justin
date: 2026-11-13
branch: feature/state-folder-180
cost: { minutes: 7, tokens: 89534 }
id: l-c621bb37
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - ".github/workflows/tests.yml"
  - README.md
  - app/config.py
  - app/main.py
  - app/modules/__init__.py
  - docker-config/Caddyfile
  - docker-config/blocklist.txt
---

## Decided
Justin had the Docker install torn out (#180): image, compose files, launchers, Pulse scripts, image scan, dependabot docker entry and the README Docker sections. A Windows script copies an old install's config and data to the state folder; it skips the Caddyfile and certs.

## Tried and ruled out
Removing tools/setup_caddy.py with the rest: kept, because the HTTPS issue will reuse its certificate code (its header now says so). Removing tools/announce.py and announce.ps1: kept, they are the only way to announce Relay to caption panels.

## Watch out
Nothing starts the panel announcement now: the launchers did that. It runs only by hand (python tools/announce.py --port 8000). Docker-only code paths in tools/setup_caddy.py (RELAY_PUBLIC_*, bridge-address notes) are dead weight until HTTPS reworks it. The migration script was run only under PowerShell on macOS; the icacls calls and the docker cp path are untested on Windows. The README tests section still says venv is the only reason to create one.

In the code: tools/migrate-from-docker.ps1, tools/announce.py, tools/setup_caddy.py, README.md (Run it natively).
