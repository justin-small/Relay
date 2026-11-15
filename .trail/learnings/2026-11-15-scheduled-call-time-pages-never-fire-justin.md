---
title: "Scheduled call-time pages never fire late and fire once per service time or date, evaluated by the crew module on the host"
author: Justin
date: 2026-11-15
branch: crew-call-time-pages
cost: { minutes: 1, tokens: 2073 }
id: l-40f85f3c
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/calltimes.py
  - app/modules/crew/migrations/0003_calltimes.sql
  - app/modules/crew/__init__.py
  - tests/test_crew_calltimes.py
---

## Decided
Justin chose these for issue 229:
- Rules are either minutes before each service time, or a weekday and clock time in Planning Center's zone. Both kinds exist.
- A rule never fires late. Its only chance is the minute after its time, so a Relay that was off or a Planning Center that was unreachable then skips it.
- Each rule has the admin's own words and pages one team: people on it who serve and have a matched crew account. A minutes-before rule pages only people who serve that service time.
- Admins edit rules in the crew admin page; they live in the crew module's own tables, not core schedules.

## Tried and ruled out
- Sending a missed call time up to ten minutes late: offered, not chosen.
- Generated text with the service time, and a page action in core schedules: offered, not chosen.

## Watch out
Each occasion (a plan time or a date) is claimed in `crew_calltime_runs` before the page is sent, so overlapping ticks cannot page twice. A rule with nobody to page still records its run. A fixed rule stays quiet when nobody on that team is on the plan. Not tried against a live Planning Center.

In the code:
- `app/modules/crew/calltimes.py` (`run`, `occasions`, `GRACE_S`)
- `app/modules/crew/migrations/0003_calltimes.sql`
- `app/modules/crew/__init__.py` (`_pager` calls it every five seconds)
- `tests/test_crew_calltimes.py`
