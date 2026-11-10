---
title: "Planning Center follower: the plan is chosen by the organisation's own day, LIVE is polled fast only near a service, and Relay works out the timers"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 1, tokens: 2408 }
id: l-c81f8bb2
scope: repo
status: active
src: agent
touches:
  - tools/check_planningcenter.py
  - app/planningcenter.py
  - app/main.py
---

## Decided
- The plan followed is the one with a `service` time on the current date in the organisation's time zone (read from the Services root), because Relay's own zone can differ from the church's. With a morning and an evening plan on one day: the one being run wins, then the first not yet over, then the last. A service running past midnight or past its planned end keeps its plan, so the plan is never pulled out from under a live service.
- The operator's pick of a different plan is saved with the date it was made and lapses when that date ends, so it cannot stick to the following week. A new token clears it.
- LIVE is polled every 2 s from an hour before a service time until 15 min after its planned end, or while an item is open; every 3 min otherwise. Fast polling across all followed service types is held to half of the rate limit Planning Center last reported, by stretching the interval, so other calls (panel control) always have room.
- Service over/under = time since the service's live_starts_at, less the lengths of the `during` items already done, less what the current item has used of its own length. Pre-service items get item time left but no over/under.
- Nobody controlling, or LIVE never opened for the plan (404), is "not live", never an error.
- Items are re-read with every plan check (5 min) and once when LIVE names an item Relay has not seen, because nothing in the docs says a plan's updated_at moves when an item does.
- Item notes stay in the follower's state for the panel feed but are left out of the admin snapshot.

## Tried and ruled out
- Depending on a Services LIVE webhook: the docs show none, so the follower only polls.

## Watch out
- The docs do not list plan_times among a Plan's relationships, so the client falls back to asking each nearby plan for its times when the include comes back unlinked.
- The docs do not give the format of the organisation's time_zone. IANA and Rails names are handled; anything else falls back to Relay's zone and admin says so.
- `length` is treated as seconds and the LIVE path used is the service-type one (Live's own documented path uses series_id). Both come from the docs alone; `tools/check_planningcenter.py --live` checks them, the plan filter and the webhook list against a real account, read-only.
- The follower tests answer in shapes written from the docs, not recorded from an account.

In the code: app/pclive.py (Follower, fast_interval), app/planningcenter.py (pick_plan, timers, near_service, running, plans_from, live, items, zone_for), /api/admin/planning-center/live and /plan in app/main.py, tests/test_pclive.py.
