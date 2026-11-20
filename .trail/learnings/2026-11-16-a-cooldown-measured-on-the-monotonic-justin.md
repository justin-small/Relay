---
title: "A cooldown measured on the monotonic clock must start at minus infinity, because CI machines have only just booted"
author: Justin
date: 2026-11-16
branch: atem-discovery-multi
cost: { minutes: 9, tokens: 28931 }
id: l-13abad8f
scope: repo
status: active
src: agent
touches:
  - app/modules/atem/service.py
  - tests/test_atem.py
  - README.md
  - app/modules/atem/__init__.py
  - app/modules/atem/discovery.py
  - app/modules/atem/routes.py
  - app/modules/atem/static/atem.js
  - app/modules/atem/templates/atem.html
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
A "not more often than every N seconds" guard starts its last-run time at float("-inf"), not 0.0. time.monotonic() counts from boot, and a fresh CI runner (or a Pi just powered on) can be under a minute in, so `now - 0.0 >= 60` is false and the first run never happens. Passed on a laptop, failed on Linux CI.

## Watch out
When a test file's tail is rewritten, check that module-level `asyncio.run(...)` calls are not left twice: test_atem ran its client test twice, and the second session hit the known Windows-only ATEM reconnect failure.

In the code: app/modules/atem/service.py Device.last_refind; tests/test_atem.py sets down_since to time.monotonic() - 1000.
