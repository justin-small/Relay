---
title: ProPresenter slide-tag tests wait for the slide number to catch up and use a long settle time for fast runs
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 6, tokens: 6996 }
id: l-20281f11
scope: repo
status: active
src: agent
touches:
  - tests/test_propresenter.py
  - app/modules/propresenter/propresenter.py
  - app/modules/propresenter/slidetags.py
---

## Decided
The follower gets a slide's notes from status/slide and its number from presentation/slide_index, two separate streams. Under load (seen on the Windows test PC with the full suite in parallel) the notes land, the settle time runs out and the action fires before the number catches up, so what is shown briefly pairs the new slide's tags with the old number. The app corrects itself (the number changes the observe key, which refreshes what is shown without firing the same set again), so the test waits for the shown state to agree instead of reading it the moment the action fires.
The "fast run" check steps slides every 0.12 s and expects only the last to fire. At a 0.4 s settle a loaded machine could stall long enough between two updates for a middle slide to fire, so that part runs with settle_s 1.5 and restores 0.4 after.

## Watch out
Both failed only on Windows under the full parallel run, never alone and never on the Mac. After the fix: 4 of 4 full Windows runs clean (2026-11-16).

In the code: tests/test_propresenter.py scenario_tags; app/modules/propresenter/propresenter.py Follower._observe_tags; app/modules/propresenter/slidetags.py Tags.observe.
