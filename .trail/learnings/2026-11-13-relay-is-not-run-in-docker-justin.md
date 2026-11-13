---
title: Relay is not run in Docker any more; starting Docker Desktop for a firmware build brings back an old Relay container that panels find first
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 14, tokens: 467490 }
id: l-d24c01ba
scope: repo
status: active
src: agent
touches:
  - run.py
  - app/modules/propresenter/ppcontrol.py
---

## Decided
- Relay runs from the repo (run.py with RELAY_CONFIG, RELAY_DEMO=1 for rehearsal) or as the desktop app. It is never started in Docker for testing. Docker is used only for the panel firmware build, in Espressif's ESP-IDF container, because ESP-IDF is not installed on this Mac.

## Watch out
- Starting Docker Desktop restarts an old `live-caption-relay` container on ports 80 and 443, which also announces _relay-captions over mDNS. A freshly flashed panel connected to it (http://<mac IP> on port 80) instead of the test Relay. Quit Docker Desktop once the firmware is built, before bench-testing panels.
- The bench learning that says a panel needs the Docker setup to find Relay is out of date: Relay now announces itself from inside the app.

In the code:
- Firmware build: docker run --rm -v "$PWD":/p -w /p/firmware/relay-display espressif/idf:v5.4.4@sha256:9ce51d4a... bash -c '. $IDF_PATH/export.sh && ./build.sh --dev'
- Then: osascript -e 'quit app "Docker"'; check that nothing listens on port 80 (lsof -iTCP:80 -sTCP:LISTEN).
