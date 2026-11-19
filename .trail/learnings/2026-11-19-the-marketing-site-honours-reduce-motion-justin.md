---
title: "The marketing site honours Reduce Motion: colour changes keep running, movement stops"
author: Justin
date: 2026-11-19
branch: ports-540
cost: { minutes: 35, tokens: 405315 }
id: l-0992e381
scope: repo
status: active
src: agent
touches:
  - app/gear.py
  - app/migrations/0011_gear_racks.sql
  - app/modules/blackmagic/__init__.py
  - app/modules/blackmagic/routes.py
  - app/modules/blackmagic/service.py
  - app/network_map.py
  - docs/modules.md
  - tests/test_blackmagic.py
  - tests/test_gear.py
  - tests/test_modules.py
---

## Decided
Justin, 2026-11-19: on the "One view" mockups, keep Reduce Motion as it is. Animations that only change colour or state (camera tally, the flashing alert, the YouTube live / needs a look flip) run even when the phone asks for reduced motion, and the headline fades in instead of rising. Moving animations (scrolling strips, scrolling captions, the service-table playhead, sections rising on scroll) stop.

## Tried and ruled out
- Playing every animation regardless of Reduce Motion: offered, not chosen.

## Watch out
- "The animations never worked on my phone" on the old site was most likely Reduce Motion: in an iOS 18.7 simulator with it on, the page ran zero animations; with it off, all of them ran, scroll-driven ones included.
- The simulator's Safari could not reach the mockup server at localhost (bound to 127.0.0.1); open http://127.0.0.1:8091 instead.

In the code: site/mockups/one-view/base.css (the unconditional .pulse/.k1-.k3 rules and the reduced-motion .w fade), c-service.html (.s-ok/.s-warn/.tw outside the no-preference block).
