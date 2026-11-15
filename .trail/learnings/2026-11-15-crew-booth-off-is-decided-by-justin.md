---
title: "Crew booth-off is decided by whether capture is running, with the call time and position shown first"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 1, tokens: 1331 }
id: l-8dd32bf8
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/routes.py
  - app/modules/crew/calltimes.py
  - app/modules/crew/static/crew.js
---

## Decided
Justin answered questions on issue 234 (2026-11-15). Booth-off means capture is not running, read from the engine's running state, the same one the status feed uses. When capture runs the crew page is unchanged. When it is off, a card comes first with the person's Planning Center teams (their admin role if unmatched) and next call time, and the Checklists tab opens by default. The order is fixed, with no admin setting.

## Tried and ruled out
- No live plan item in Planning Center as booth-off: offered, not chosen.
- An operator toggle for booth-off: offered, not chosen.
- A compact live view while capture runs: offered, not chosen.
- An admin setting to turn the booth-off layout off: offered, not chosen.

## Watch out
The call-time occasions ignore which service times a person serves, so the next-call lookup filters by who serves each time; keep that filter.

In the code: `app/modules/crew/routes.py` (`/crew/api/today`), `app/modules/crew/calltimes.py` (`next_for`), `app/modules/crew/static/crew.js` (`loadToday`).
