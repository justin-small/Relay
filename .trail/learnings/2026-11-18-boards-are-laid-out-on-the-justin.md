---
title: "Boards are laid out on the Boards page now, the server decides which page every screen shows, and a page picked by hand holds until the automatic choice changes"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 23, tokens: 360272 }
id: l-27790ec9
scope: repo
status: active
src: agent
touches:
  - app/boards.py
  - app/modules/__init__.py
  - app/modules/propresenter/__init__.py
  - app/static/board-live.js
  - app/static/board.js
  - app/static/boards.css
  - app/static/boards.js
  - app/main.py
---

## Decided
Built 2026-11-18 from the chosen mockup (B in the admin shell), choices made without asking Justin:
- The editor moved off the board screen into the Boards page; /board/<slug> is only the board, and an old ?edit=1 link sends an admin to /admin#boards.
- The server decides the page (one, rotate, schedule, follow), so every screen agrees. A rotation is turned by each screen from the server's clock (page = time / every), so screens turn together without the server keeping state.
- A page chosen by hand on Run (boards.manage) holds until what decides the page changes (the schedule row, the matched ProPresenter rule); in Rotate or One page mode it holds until "Back to automatic".
- "Follow ProPresenter" matches the presentation name, the slide group or the slide label, case-insensitive; Justin's word was "slug", which is still to confirm.
- Screens report a random id kept in localStorage on each poll; names live on the board (screen_names), not in the export. A save changes the board's rev and the screens reload themselves; "Reload all screens" bumps a counter.
- The shared editor got opt-in tray, card and bar options so the lighting cue pages stay as they were.

## Watch out
- Follow was tested against the follower's state, not a live ProPresenter.
- The style ratchet counts hex in JS strings, so the card's colour swatches come from the server (boards.SWATCHES).
- `.rl-editing .rl-canvas .rl-cell` sets z-index 1 with higher specificity than `.rl-cell.rl-sel`; the tray mode raises the selected cell itself, or its settings card hides under later widgets.

In the code: app/boards.py (pages, clean_rotation, current, templates), app/main.py board routes (_board_state, _board_live, /api/admin/boards/live, /screens/{sid}, /reload, /page), app/static/boards.js + boards.css (the page), board-live.js (feeds shared by screen and preview), board.js (screen), layout-editor.js (tray, card, bar), Module.board_follow in app/modules/__init__.py and propresenter/__init__.py.
