---
title: "Lighting page is four tabs, and a cue page is made in a New page step then edited on its own screen"
author: Justin
date: 2026-11-18
branch: lighting-tabs
id: l-9a0d68ea
scope: repo
status: active
src: agent
touches:
  - app/modules/lighting/backend.py
  - app/modules/lighting/lightkey.py
  - app/modules/lighting/qlcplus.py
  - app/modules/lighting/service.py
  - app/modules/lighting/static/lighting.css
  - app/modules/lighting/static/lighting.js
  - app/modules/lighting/templates/lighting.html
  - app/modules/lighting/thelightingcontroller.py
  - app/modules/lighting/vista.py
  - tests/test_lighting.py
---

## Decided
Justin, 2026-11-18: of three lighting mockups (tabs, desk, page library) he chose the tabbed one: Fire cues, Pages, Screens, Program. He asked to change how a page is added and edited:
- Pages tab is a list (preview, counts, screen state, Edit); order is changed by dragging a row or its arrows.
- New page is its own step: name, a size picked from grid pictures, and a start (empty, copy a page, or tick cues that are laid out in order), with a live preview, then Create and edit.
- Editing opens one page on its own screen ("← Pages / name") using core's layout editor, with an Unsaved changes badge and a confirm on leaving; screen switch, Duplicate and Delete sit in Page settings below.
- The Program tab shows only the chosen program's fields. Each backend declares them (fields, default_port, about), sent in the admin snapshot.
- The tab bar is full width like Planning Center's (#page-lighting .tabs display:flex).
In the code: mockups in docs/mockups/lighting/ (a-tabs.html chosen); app/modules/lighting/templates/lighting.html, static/lighting.js, static/lighting.css; Backend.fields in backend.py.

## Tried and ruled out
B (lighting desk with setup drawer) and C (page library with setup checklist): not chosen.
Per-page press counts and a press log from the mockups: left out, because they would need Relay to record presses per page.

## Watch out
The UI demo (docs/ui-audit/demo/serve.py) versions static files when it starts, so restart it after a CSS change to see it.
