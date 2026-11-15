---
title: "The operator panel allows framing by itself only (SAMEORIGIN), so the overlay builder's preview works"
author: Justin
date: 2026-11-15
branch: tests/platform-improvements
cost: { minutes: 26, tokens: 305838 }
id: l-2c4f05a9
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/calltimes.py
  - app/modules/crew/routes.py
  - app/modules/crew/static/crew.css
  - app/modules/crew/static/crew.js
  - app/modules/crew/templates/crew.html
  - tests/test_crew_calltimes.py
  - tests/_harness.py
  - tests/run_all.py
  - ".coveragerc"
  - tests/test_realtime.py
---

## Decided
Justin chose (2026-11-15) to send X-Frame-Options: SAMEORIGIN instead of DENY on the operator panel. The overlay builder previews the real /overlay page in a frame on the panel, and DENY blanked that preview for every role. Other sites still cannot frame the panel.

## Watch out
The browser test (tests/test_browser.py) signs in as every role and found this, along with a second bug: admin.js's $() never returns null (it hands back a stand-in element), so `if ($('id'))` is always true. Use document.getElementById when checking whether a section is on the page.

In the code: PANEL_HEADERS in app/main.py; asserted in tests/smoke_test.py and tests/test_serving.py; loadAlertHistory in app/static/admin.js.
