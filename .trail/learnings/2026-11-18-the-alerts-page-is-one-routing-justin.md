---
title: "The Alerts page is one routing grid: what the band can send down the side, every panel and screen across, with a Test per screen"
author: Justin
date: 2026-11-18
branch: sidebar-by-module
cost: { minutes: 17, tokens: 443970 }
id: l-b290ed03
scope: repo
status: active
src: agent
touches:
  - app/alerts.py
  - app/main.py
  - tests/test_alerts.py
  - app/static/admin.js
  - tests/test_auth.py
  - app/static/admin.css
---

## Decided
- Justin, 2026-11-17: of three redesigns (a routing grid, a phone-and-screen preview, places in the building) he chose the grid. Rows are alert buttons, preset stage messages and "anything typed"; columns are panels that checked in plus browser screens (named in config, open now, or added on the page). A ticked square puts that column in the row's panels or screens. Config shape is unchanged.
- Test sends "Test from Relay" to one column as a steady info stage message: nothing flashes, nothing to tap, not written to the alert history (it is audited as alerts.test). It answers how many streams took it, so the page can say "not connected".
- Settings (auto-stop, message/banner seconds, replies, sign-in) and History moved into collapsed sections; the sender link and Save sit at the top.

## Tried and ruled out
- Places in the building (Claude's recommendation) and a two-device preview: offered, not chosen.

## Watch out
- A browser screen added on the page, or unticked everywhere, stays as a column only until reload; it is saved only once some row ticks it.
- The grid rebuilds only when its rows, columns or open options row change, and never while a text field has focus; checkbox clicks edit alertCfg in place.

In the code: app/alerts.py Alerts.test and TEST_TEXT; POST /api/admin/alerts/test in app/main.py (AUDIT_ROUTES, tests/test_auth.py); app/static/admin.js buildAlertGrid/alertColumns; table.apatch in app/static/admin.css; tests in tests/test_alerts.py "test a screen from the Alerts page".
