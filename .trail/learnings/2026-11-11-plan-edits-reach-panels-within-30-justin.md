---
title: "Plan edits reach panels within 30 seconds near a service because the plan is re-read on a timer, not on change"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 2, tokens: 64319 }
id: l-421da561
scope: repo
status: active
src: agent
touches:
  - app/pclive.py
  - tests/test_pclive.py
---

## Decided
Panels were slow to show plan edits (item titles, lengths, order) because Relay re-read the plan and its items only every 5 minutes. Items were otherwise fetched again only when the LIVE current item was missing from the list, so an edit to an existing item waited for the timer. Near or during a service the re-read now runs every 30 seconds; outside that window it stays at 5 minutes to keep API use low. Pushing to panels was never the slow part: the service feed pushes as soon as the follower's state changes.

## Tried and ruled out
Re-reading items only when the plan's updated_at changes: not used, because it isn't confirmed that editing an item bumps the plan's updated_at.

## Watch out
The tests are standalone scripts that exit at import, so `pytest tests` fails while collecting them. Run each one with the project venv instead. A LIVE webhook (services.v2.events.plan.live.updated) would bring updates down to near-instant, but Relay would have to be reachable from Planning Center.

In the code: app/pclive.py PLAN_CHECK_S=300, PLAN_CHECK_FAST_S=30, set in Follower._check_plan and pulled forward in _poll_live when tr.fast. Tests: .venv/bin/python tests/test_pclive.py (also test_servicefeed, test_pccontrol, test_planningcenter).
