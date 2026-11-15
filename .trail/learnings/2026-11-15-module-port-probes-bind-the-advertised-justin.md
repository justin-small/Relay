---
title: "Module port probes bind the advertised address, and tests that run scripts or check file modes skip Windows"
author: Justin
date: 2026-11-15
branch: intercom-data-and-livekit-237
cost: { minutes: 5, tokens: 19041 }
id: l-88af2975
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/livekit.py
  - tests/test_intercom.py
  - app/modules/intercom/__init__.py
  - app/modules/intercom/migrations/0001_intercom.sql
  - app/modules/intercom/routes.py
  - app/modules/intercom/static/intercom.js
  - app/modules/intercom/store.py
  - app/modules/intercom/templates/admin.html
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
A check that a port is free binds the address the service will advertise (the LAN address, or loopback), never 0.0.0.0: CodeQL blocks the pull request on any bind to all interfaces. Tests that check file modes (0600) or run a fake program by its shebang line run on Linux and macOS only; the Windows runner has neither.

## Tried and ruled out
Probing 0.0.0.0 so any interface conflict shows: fails the required CodeQL check.

## Watch out
Windows CI is the only place these show up; a Mac passes both. On Unix the TCP probe sets SO_REUSEADDR, or a connection just closed makes the port look busy.

In the code: busy_port in app/modules/intercom/livekit.py; the POSIX guard in tests/test_intercom.py.
