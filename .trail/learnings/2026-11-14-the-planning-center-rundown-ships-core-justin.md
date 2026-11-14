---
title: "The Planning Center rundown ships core and further scope together, /screen offers full and side views, and mics map to positions with a random draw when severa…"
author: Justin
date: 2026-11-14
branch: pco-rundown-148
id: l-bd0e1705
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/pclive.py
  - app/main.py
---

## Decided
Justin, 2026-11-14, issue 148 (mockups awaiting approval):
- One branch builds both the core scope (clock times, teams, leader, mic, LIVE control) and the issue's "Further scope" list (three layouts, photo cards, service-time rosters, sermon notes, item timing).
- /screen shows the rundown in two ways, picked by URL: a full view that replaces the captions, or a side column beside the captions.
- Mics are mapped to Planning Center positions in Relay's admin. A position can have several mics; when several people accept that position, each gets one of its mics at random. The draw is made once per plan and service time and stays put. It is redrawn only when someone accepts or drops out, or when the operator presses Redraw.
- Operator LIVE controls (Previous, Next, Take control, Release) live in a Rundown section on the Planning Center module's admin page.

## Tried and ruled out
- Core scope only, with further scope as a follow-up issue: not chosen.
- A mic per song typed in an item note, or a note overriding the position map: not chosen. The position map needs no weekly typing.
- LIVE controls as a card on the main Live dashboard: not chosen.

## Watch out
- Re-drawing mics on every poll would move people between mics mid-service. Store the draw per plan and service time.
- Previous and Next on /screen are proposed only for a signed-in operator; a kiosk with no sign-in stays read-only (not yet confirmed).

In the code:
- Built in app/modules/planning_center/pclive.py, pccontrol.py and templates/admin.html; /screen (page_screen in app/main.py) gets it through a module hook only.
- Mockups: docs/mockups/rundown/screen.html, admin-rundown.html (branch pco-rundown-148).
- Proposed URL options: /screen?rundown=full|side&layout=complete|compact|fit&cards=person|position&team=<name>&time=0900
