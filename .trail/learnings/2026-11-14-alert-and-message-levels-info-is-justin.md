---
title: "Alert and message levels: info is steady blue, attention flashes amber, urgent flashes red and white; set per button or preset and overridable by the sender"
author: Justin
date: 2026-11-14
branch: alerts-levels-215
cost: { minutes: 5, tokens: 154114 }
id: l-60f3c3ba
scope: repo
status: active
src: agent
touches:
  - app/alerts.py
  - app/static/alert-screen.js
---

## Decided
Justin chose for issue 215 (2026-11-14): the level comes from the button or preset, and the sender can override it for one send from the /alert page. Info is a steady blue, attention flashes amber and dark, urgent keeps red and white. Existing alert buttons stay urgent and stage messages default to info, so nothing changes until the operator picks. The first tap still stops any level everywhere. The flash stays at once a second.

## Tried and ruled out
- Putting each button's level in the alert board the panels read: the board is about 3 KB, and the level is only needed on the alert event the panel is sent.

## Watch out
- The panel drawing (alert_view.cpp) is not compiled or run here: only the parsing and colours (alert_format.h) are in the native checks. Flash a panel before trusting the colours.
- The wire sends hz 0 for info; older firmware reads that as 1 and ignores level, so it still flashes red and white until it is updated.
In the code: app/alerts.py (LEVELS, press, send_message), app/static/alert-screen.js, firmware alert_format.h (alert_level).
