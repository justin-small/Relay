---
title: "Stage messages overlay the panel's current feed, are sent from the alert page, and are cleared by timer or from the sender page"
author: Justin
date: 2026-11-14
branch: alerts-stage-messages-214
cost: { minutes: 6, tokens: 175658 }
id: l-d017d8d6
scope: repo
status: active
src: agent
touches:
  - app/alerts.py
  - app/main.py
  - app/panels.py
  - app/static/admin.js
  - app/static/alert-screen.js
  - app/static/alert.js
  - app/static/viewer.css
  - app/templates/admin.html
  - app/templates/alert.html
  - app/templates/viewer.html
---

## Decided
Justin chose for issue 214 (2026-11-14): a stage message draws over whatever feed a panel shows, so it does not take one of the two feed slots. Presets and typed words are sent from the existing alert page. A message stays 10 s and a banner 5 min by default, both set by the operator. Nothing on the panel is tapped to dismiss it, and there is no seen tracking (that is issue 216). Anyone who may send alerts may send messages, and sign-in follows the alert setting.

## Tried and ruled out
- A third kind of feed taking a slot: it would replace a real feed while shown and cost panel memory.
- Free text only in admin, or a separate page: not chosen.
- Putting messages in the alert board the panels read: the board is about 3 KB and parsed in place, so messages have their own stream for the sender page and their own panel events.
- First tap clears everywhere, as alerts do: messages are information, not a request.

## Watch out
- The panel overlay is not tested on hardware. Only the parsing runs in the native checks.
- In the code: app/alerts.py (Message, send_message), app/main.py (/api/alerts/messages/*), app/static/alert-screen.js, firmware alert_view.cpp (alert_view_message).
