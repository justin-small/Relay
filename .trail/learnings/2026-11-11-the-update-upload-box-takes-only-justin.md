---
title: "The update upload box takes only the app image; the merged USB image is uploaded on the flasher page, which Panels links to prominently"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 2, tokens: 22200 }
id: l-963b24a2
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/panelfw.py
  - app/templates/admin.html
  - tests/test_panels.py
---

## Decided
Justin chose to keep the Panels "Panel firmware" upload for relay-display-app.bin only, and to make the way to the flasher page obvious instead: a blue whole-card link at the top of the Panels page ("Set up a new panel over USB"). He had uploaded relay-display.bin into the update box because he did not see the flasher link.

## Tried and ruled out
Letting the update box accept relay-display.bin too (storing its application as the update and the whole file for the flasher): Justin rejected it. The two images stay uploaded in their own places.

## Watch out
The update box's refusal of a merged file still says to upload relay-display-app.bin; it does not mention the flasher.

In the code: app/templates/admin.html (.flashcta card on the Panels page), app/main.py admin_panel_firmware_upload (unchanged, SLOT_BYTES limit), /admin/flash upload via /api/admin/panels/flash.
