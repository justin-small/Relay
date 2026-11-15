---
title: "Alert replies, history and events out: one global reply list, 30 days of history for admins, one module for webhook, OSC and Companion state; sender PIN deferr…"
author: Justin
date: 2026-11-15
branch: alerts-replies-history-events-216-219
cost: { minutes: 7, tokens: 205677 }
id: l-ec1f5dd6
scope: repo
status: active
src: agent
touches:
  - app/alerts.py
---

## Decided
Justin answered questions on issues 216-219 (2026-11-14): replies are one global list (up to 6 of 24 characters) and optional, so a plain tap still acknowledges; history keeps 30 days and is read by Admins and Owners (own permission alerts.history); one Alert events module carries the webhook, OSC and the Companion state read; the sender PIN (#218) is skipped in favour of the user system (#175). Issue 215 was already merged.
In the code: app/alerts.py (replies, listeners, _emit), app/alerthistory.py, app/migrations/0005_alert_history.sql, app/modules/alert_events/.

## Tried and ruled out
- Three small modules (webhook, OSC, Companion): one module is simpler to switch on and test.

## Watch out
- Panel firmware does not show the reply buttons: the alert event now carries `replies`, only browser screens (alert-screen.js) use it.
- The Companion module lives in a separate repo; Relay only offers GET /api/admin/alert-events/state (needs an API token), the Companion side is not written.
- The webhook posts to whatever address an admin types; CodeQL may flag it as a server-side request, as it did for the signage link check.
- Listeners are added in attach(), so tests must call MODULE.attach() themselves.
