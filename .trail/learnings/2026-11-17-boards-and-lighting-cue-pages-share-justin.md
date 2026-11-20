---
title: "Boards and lighting cue pages share one layout editor in core, on a snapping grid, shipped as one pull request"
author: Justin
date: 2026-11-17
branch: boards-layout-editor
id: l-0a52f895
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-16, issues 421 and 156:
- One pull request for both. The shared editor, style schema and renderer are core; boards and lighting cue pages both use them, and any future surface (panel buttons #223, crew pages) must reuse them rather than build its own.
- Boards live in core (/board/<slug>, core widgets: captions per language, now/next, countdown, alerts, status, clock, text, board link). Modules add widgets through a Module method (like network_map), never imported by core; a module that is off leaves the palette and its widgets say so on the board.
- Layout is a grid with snap: whole cells, per-board columns/rows (default 12 x 8) and shape (16:9, 4:3, 9:16). No overlapping items.
- Any signed-in user may view a board; a board marked Public opens without sign-in. Editing is admin only (boards.manage). Web only: no panel firmware.
- Lighting cue pages use the shared editor instead of their own form and preview.

## Tried and ruled out
- Two pull requests (editor first, boards after): Justin wanted one change so the two cannot drift apart.
- Boards as a switchable module: boards are core; only integrations are modules.
- Free x/y/w/h positioning, alone or with the grid: the grid lays out the same on every screen size.
- A separate board.view permission and firmware rendering: not needed.

## Watch out
- Padding in container-query units (cqmin) on the size container itself resolves against the viewport and squashes the text inside to almost nothing; use px/clamp for the container's own padding.
- A <button> cannot be a size container; render pressable items as a div with role=button.

In the code: mockup at docs/mockups/boards/index.html (LayoutEditor and renderItem stand in for app/static/layout-editor.js and layout-render.js); planned core files app/layout.py and app/boards.py.
