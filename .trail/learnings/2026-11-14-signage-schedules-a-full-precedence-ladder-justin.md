---
title: "Signage schedules: a full precedence ladder with takeover and tag left as hooks, screen slots beat group slots, hand overrides end at the next scheduled change"
author: Justin
date: 2026-11-14
branch: main
cost: { minutes: 5, tokens: 151152 }
id: l-75738a49
scope: repo
status: active
src: agent
touches:
  - app/modules/signage/store.py
---

## Decided
Issue 206 builds the whole ladder now (takeover, tag, hand override, schedule, default) and the "why" text, but takeover and tag are empty hooks that #207 and #208 fill in. A screen's own slots beat its group's, which beat all-screens slots; a date range beats a weekly slot; of two overlapping slots the later start wins. Slots may cross midnight and use wall-clock time in a US zone chosen in Settings, so DST keeps the clock time. A hand override is set by any signage manager; one module-wide setting picks "until the next scheduled change" (default) or "until cleared". The end time is worked out when the override is set by scanning forward minute by minute (8 days), so it needs no lazy state.

## Tried and ruled out
Schedule-only now (no ladder): would make #207/#208 reshape the resolver. Merging slots by specificity per moment, or one owner per screen: harder to explain on the admin page. Reject overlaps or manual priority numbers: more operator work than "later start wins". Per-screen override lifetime: more settings than needed.

## Watch out
An override's end time is fixed when it is set; editing slots afterwards does not move it. If the schedule never changes within 8 days it lasts until cleared. How a tag and a hand override interact is left to #208 (tag is checked first).

In the code: app/modules/signage/schedule.py (matching, register_layer, next_change), store.resolve and override_set in store.py, migration 0002_schedule.sql.
