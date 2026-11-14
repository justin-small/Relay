---
title: "A test Relay is run from the checkout with its own state folder, not restarted through Docker's start script"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 3298 }
id: l-4f8be012
scope: repo
status: active
replaces: l-a570bd43
src: agent
touches:
  - run.py
  - app/netinfo.py
---

## Decided
There is no start.command, RELAY_ADMIN_IPS or RELAY_UID any more (removed in #180/#187). Relay reads this machine's LAN addresses itself (app/netinfo.py) and runs as the user who starts it, so the old trap (viewer links showing only 127.0.0.1, container running as uid 10001) cannot happen. Run a test copy with RELAY_HOME=<empty 0700 folder> python run.py; add RELAY_DEMO=1 for rehearsal and RELAY_ANNOUNCE=0 so caption panels do not find it.

## Watch out
Give a test copy its own "port"/"admin_port" in its config.json when a real Relay (the desktop app) is running on the same machine, or it falls back to 8080/8443 and may clash there too. Python 3.13+ is needed; the repo's .venv on this Mac is 3.10 and cannot import the app.

In the code: run.py, app/paths.py (RELAY_HOME), app/serving.py (port fallback).
