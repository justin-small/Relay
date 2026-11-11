---
title: "Alerts (#121): musicians send from /alert or a panel's Alert feed, the first tap anywhere stops it everywhere, and the flash is fixed at once a second"
author: Justin
date: 2026-11-11
branch: alerts-121
cost: { minutes: 23, tokens: 392018 }
id: l-9ccbf5ac
scope: repo
status: active
src: agent
touches:
  - app/alerts.py
  - tests/test_alerts.py
  - app/templates/alert.html
  - app/static/alert.js
  - app/static/alert-screen.js
  - firmware/relay-display/main/alert_format.h
  - firmware/relay-display/main/alert_client.h
  - firmware/relay-display/main/alert_client.cpp
  - firmware/relay-display/main/alert_view.h
  - firmware/relay-display/main/alert_view.cpp
---

## Decided
Justin settled the open questions from a mockup round (2026-11-11): senders are a phone page (/alert, viewer port, anyone with the link, a name asked once and kept in localStorage) and a panel set to the new Alert feed. Targets are named groups set in admin (Alerts page): panels by id and browser screens by name (/screen?alert=<name>). The first tap on any target acknowledges for the whole group, and the sender sees "Seen on <where>". The flash is red/white once a second, fixed (panels clamp anything Relay says to 2 Hz, under the 3 Hz seizure threshold). Unacknowledged alerts auto-stop after an admin setting (30 s to 10 min, default 2 min). Pressing a flashing button again cancels it; a second press within 0.8 s is a double tap, not a cancel.

## Tried and ruled out
Per-screen acknowledgement ("Seen 2 of 3"): dropped for first-tap-stops-all. A small bell on every panel pane: dropped for an Alert feed the operator sets, like Service.

## Watch out
The panel's alert_board is about 3 KB: it is parsed in place and never built on a task stack (the caption task has 6 KB), as with the Service state. The flash draws on lv_layer_top from its own LVGL timer, so it covers any view including boot and setup. A panel that loses Relay stops flashing 5 s after the auto-stop by itself. Montserrat 48 was enabled for the sender's name on the flash.

In the code: app/alerts.py (state, validation, streams), app/main.py (/alert, /stream?stream=alert, /api/alerts/*, /api/admin/alerts), app/static/alert.js and alert-screen.js, firmware main/alert_format.h (rules, natively checked), alert_view.cpp, alert_client.cpp, kFeedAlert = 4 in settings_format.h. Tests: tests/test_alerts.py, check_alerts in test_native.cpp.
