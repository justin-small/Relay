---
title: "Parent paging pages a Check-Ins security code from a no-login room link, and the operator picks how it reaches ProPresenter"
author: Justin
date: 2026-11-15
branch: propresenter-parent-paging-220
id: l-2420d7f3
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/modules/planning_center/planningcenter.py
  - tests/test_auth.py
---

## Decided
Justin answered questions on issue 220 (2026-11-14), changing it from a typed number to picking a child:
- Kids workers open a per-room link (secret in the URL, no sign-in, QR on the admin page); each link is fixed to one Check-Ins room (location). "New link" replaces the secret and the old link stops at once.
- The list is the kids (filter attendee, not volunteers) checked in to that room and not checked out. The screen and the operator's queue get only the security code and room name, never the child's name.
- How a page reaches ProPresenter is a setting with all three ways: queue (operator presses Show on Relay's Parent paging page, then Relay triggers the message), fill (Relay only writes the code into the message's text token), auto (triggered at once). All codes up share one message token.
- A page ends by operator clear, the worker's "Parent arrived", or a timeout in minutes (0 = never). Pages live in memory.
- Web page now; the panel firmware (relay-display) list is a follow-up.
- Its own module (parent_paging) with uses=("planning_center", "propresenter"), not inside the ProPresenter module, because it joins both.

## Tried and ruled out
- The ProPresenter stage message to notify the operator: the stage display is seen by people on stage, not the booth. The admin page plays a tone instead.

## Watch out
- Room pages list children's names, so they 404 on the viewer port and through the tunnel (cf-* headers), like the rundown; they are only on the HTTPS port.
- Check-Ins paths, filters and fields come from the published OpenAPI description (https://api.planningcenteronline.com/check-ins/v2/open_api/2025-05-28), not a live account. Calls send X-PCO-API-Version 2025-05-28 only for /check-ins/ paths. fields[CheckIn] keeps medical notes and emergency contacts out.
- The ProPresenter message trigger body (array of tokens), PUT /v1/message/{id} and GET .../clear come from the API reference, not tested on a real ProPresenter.
- The page code is taken from Relay's cached list for the room, never from the browser, so a link can only page a child in its room.
- The admin script redraws its lists only when they change; redrawing every poll swapped buttons out from under clicks.

In the code: app/modules/parent_paging/ (checkins.py, paging.py, routes.py, templates/room.html), app/modules/planning_center/planningcenter.py CHECK_INS_VERSION, tests/test_parent_paging.py, tests/test_auth.py.
