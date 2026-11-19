---
title: "Named schedules are picked by ref, there is one Relay time zone, and a schedule in use cannot be deleted"
author: Justin
date: 2026-11-19
branch: schedules-page-and-relay-timezone
cost: { minutes: 1, tokens: 0 }
id: l-72b603d6
scope: repo
status: active
src: agent
touches:
  - app/schedules.py
  - app/scheduler.py
  - app/config.py
  - app/modules/signage/store.py
  - app/modules/crew/calltimes.py
---

## Decided
A named schedule is a name plus weekly windows and one-off dates, and can also be open around each Planning Center service time (30 min before to 120 after by default, editable). Features store a ref `schedule:relay:<id>` instead of their own days and times: the engine's start/stop (config `engine_schedules`), a signage slot, a board rotation row, a crew call time (N minutes before each window starting today). One Relay time zone (config `timezone`, set under System) replaces the per-schedule and signage zones; an older config's first schedule zone, else the signage zone, carries over. Deleting a schedule anything uses is refused with the list of where. Planning Center keeps its own organisation zone first and falls back to the Relay zone.
## Tried and ruled out
A "used by" list kept in core: core cannot import modules, so each module provides `schedules.used.<module>` and core asks. A zone per feature: removed.
## Watch out
The migrated engine list holds every old schedule, so each shows "used by Engine" and must be unticked there before it can be deleted. Signage resolves a screen by stepping minute by minute across a week, so `store.open_ref` caches a schedule's windows for a minute; do not call `schedules.is_open` in that loop. A hidden `.hint` is still drawn (components.css sets display): empty its text instead.
In the code: app/schedules.py, app/scheduler.py (`used_by`, `plan_source`), app/config.py (migration), app/modules/signage/store.py (`open_ref`), app/modules/crew/calltimes.py (`_schedule_occasions`).
