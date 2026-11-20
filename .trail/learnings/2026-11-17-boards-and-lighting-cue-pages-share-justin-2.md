---
title: "Boards and lighting cue pages share one layout editor in core on a snapping grid, and free-placed lighting pages moved onto it"
author: Justin
date: 2026-11-17
branch: boards-layout-editor
id: l-838baafa
scope: repo
status: active
replaces: l-0a52f895
src: agent
touches:
  - app/config.py
  - app/main.py
  - app/modules/__init__.py
  - app/modules/lighting/__init__.py
  - app/modules/lighting/routes.py
  - app/modules/lighting/service.py
  - app/modules/lighting/static/lighting.js
  - app/modules/lighting/static/screen.js
---

## Decided
Justin, 2026-11-16, issues 421 and 156, one pull request:
- One editor, renderer and style schema in core; boards and lighting cue pages use it, and later surfaces (panel buttons #223, crew pages) must reuse it.
- Boards are core. Modules add widgets through Module.board_widgets / board_data / board_press; core never imports them. A module that is off leaves its widgets on the board, drawn as off.
- Grid with snap only, whole cells, no overlap. Lighting pages saved with free placement (#379) are converted once to the nearest cells of an 8 x 6 grid; old grid pages are laid into four columns in order. The free option is gone.
- Anyone signed in (boards.view, member) opens a board on the operator port; a public board opens for anyone on either port. boards.manage (admin) edits. Pressing a module's button needs its press permission even on a public board. Web only.

## Tried and ruled out
- Two pull requests, boards as a module, free positioning, a separate view permission, panel firmware: Justin's choices.
- Keeping a free mode for lighting only: Justin chose converting to the grid.

## Watch out
- Board presses are not under /api/admin, so the audit middleware misses them: the press route writes the module's audit.note entries itself.
- cq units in a size container's own padding resolve against the viewport and crush the text inside; a <button> cannot be a size container (render pressables as div role=button).

In the code: app/layout.py, app/boards.py, app/static/layout-render.js, layout-editor.js, layout.css, board.js, boards.js, templates/board.html; lighting service.clean_pages and _legacy_places.
