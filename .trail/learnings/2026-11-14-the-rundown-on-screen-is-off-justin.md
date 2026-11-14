---
title: "The rundown on /screen is off until switched on, refused through remote access, and shows no radio telemetry"
author: Justin
date: 2026-11-14
branch: pco-rundown-148
id: l-52140c2d
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/main.py
  - app/modules/planning_center/__init__.py
  - app/modules/planning_center/pccontrol.py
  - app/modules/planning_center/pclive.py
  - app/modules/planning_center/planningcenter.py
  - app/modules/planning_center/routes.py
  - app/modules/planning_center/templates/admin.html
  - tests/test_auth.py
  - tests/test_pclive.py
---

## Decided
Built for issue 148 (2026-11-14), after Justin approved the flat, one-screen mockups:
- The "rundown" stream that /screen reads is refused (403) until the operator ticks "Show the rundown on /screen". It carries volunteers' names, photos and sermon notes, and /screen is on the viewer port with no login.
- It is also refused for any request that came through the remote access tunnel (Cloudflare adds cf-ray / cf-connecting-ip to everything it forwards). The tunnel points at the viewer port, so without this check names would be reachable from the internet.
- /screen is display-only: no scrolling and no buttons. Previous, Next, Take control and Release are on the admin page's Rundown section (same rules as a panel's press, logged with the panels' presses) and on panels.
- The core page (rundown.html, rundown.js, rundown.css) lays out whatever a module sends on the "rundown" stream, the same pattern as lyrics. Core never imports the module and never names its routes.
- Who is scheduled (team members, needed positions) is re-read on its own 2-minute timer, never on the fast LIVE poll.
- Mic draws are saved in config.json under planning_center.rundown.draws, so they hold across restarts.

## Tried and ruled out
- Meters, frequency, RF and battery on the micboard: the mockup showed made-up numbers. Relay has no wireless receiver integration, so the real micboard shows names and mics only. A receiver module would be separate work.

## Watch out
- The PlanPerson "times" relationship (which service times a person serves) and the NeededPosition "time" relationship were taken from the API docs and a community client, not checked against a live account. When a row has no times relationship, the person is treated as serving every time.
- Photos are Planning Center's own https thumbnails, loaded straight by the kiosk browser. A screen with no internet shows initials.

In the code:
- app/modules/planning_center/rundown.py (build, draw_mics, clean_rundown); routes.py (rundown_now, via_tunnel, admin routes); pccontrol.Control.press_admin
- app/main.py _rundown_page and RUNDOWN_STREAM; app/templates/rundown.html; app/static/rundown.js
- tests/test_pcrundown.py
