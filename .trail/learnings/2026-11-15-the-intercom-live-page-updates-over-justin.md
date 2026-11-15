---
title: "The Intercom live page updates over server-sent events, mutes live in memory by user, and loss/jitter use placeholder thresholds until #276"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 6, tokens: 150395 }
id: l-79abfb12
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - app/static/admin.js
  - tests/test_auth.py
---

## Decided
Justin answered questions on issue 241 (2026-11-15): packet loss and jitter columns use hardcoded placeholder thresholds because #276 does not exist yet; mutes are keyed by user, in memory, and clear on restart; the top bar pill is contributed by the module through a generic core hook, so core never names intercom. He chose an admin WebSocket for live updates, but it was built as server-sent events like every other admin stream here (works with the cookie sign-in); say so if that matters.

## Tried and ruled out
- Skipping the loss/jitter columns until #276: offered, not chosen.
- Mutes keyed by device: offered, not chosen.
- Pill only on the live page: offered, not chosen.

## Watch out
Loss is a packet count (phones send no percentage). The Local/Remote column is always "Local" until remote phones exist. Not opened in a browser; the 1 s and 20-phone targets are unmeasured.

In the code:
- Thresholds LOSS_PACKETS and JITTER_MS in app/modules/intercom/live.py; swap for #276's.
- Module.pills() in app/modules/__init__.py, rendered by paintModulePills in app/static/admin.js.
- Permission intercom.live (operator); routes under /api/admin/intercom/live, listed in tests/test_auth.py.
