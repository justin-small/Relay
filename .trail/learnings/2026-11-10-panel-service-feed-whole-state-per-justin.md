---
title: "Panel Service feed: whole state per message, timers sent as of Relay's clock, notes and error reasons kept off the viewer port"
author: Justin
date: 2026-11-10
branch: pc-service-feed-79
cost: { minutes: 6, tokens: 136851 }
id: l-723e2ac5
scope: repo
status: active
src: agent
touches:
  - app/servicefeed.py
  - tests/test_servicefeed.py
  - app/main.py
---

## Decided
- Each `service` message carries the whole state (plan, items, current/next, timers, controller), so a reconnecting panel needs nothing else. A new one goes out only when something shown changes; the clock-only fields (server_time, updated_at, item_left_s, service_over_s) are left out of that comparison, or every 2 s LIVE poll would resend.
- Between changes a small `service_tick` every heartbeat (15 s) carries Relay's clock and when Planning Center last answered, instead of an SSE comment, so the panel can resync its countdown and show how fresh the data is.
- Timers are sent as they stood at server_time; the panel counts down itself. Over/under only grows once the current item has run past its length (formula in app/servicefeed.py's docstring).
- A panel gets `reachable` only, never the error text: the reason (bad token, rate limit) stays in admin, because the feed is on the viewer port with no login.
- Only followed service types can be opened (404 otherwise), so the open feed cannot make Relay ask Planning Center about anything else.
- Item notes are sent only while "Show item notes on panels" is on (config planning_center.show_notes, off by default); flipping it re-sends at once. A token save keeps the switch.

## Watch out
- Admin routes call follower.poke(), which runs a real tick: tests that set follower tracks by hand must stub poke, or the tick rebuilds the tracks and tries to reach Planning Center.
- `service` entries ride /api/targets and the caption `targets` message; firmware reads targets by key, so older panels ignore them. The panel's targets buffer is 4 KiB.

In the code: app/servicefeed.py (ServiceFeed, feed), /stream?stream=service in app/main.py, /api/admin/planning-center/notes, tests/test_servicefeed.py.
