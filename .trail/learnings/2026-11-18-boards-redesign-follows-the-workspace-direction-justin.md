---
title: "Boards redesign follows the workspace direction inside the operator panel, with places, templates, screens watching and pages that rotate, follow a schedule or…"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 1, tokens: 1601 }
id: l-03b2eee5
scope: repo
status: active
src: agent
touches:
  - app/static/admin.css
  - app/boards.py
  - app/static/boards.js
  - app/templates/admin.html
---

## Decided
Justin, 2026-11-18: of three Boards redesigns (gallery of live thumbnails, workspace inside the operator panel, places-and-templates with zones), he chose the workspace one (B) and asked to see it inside the admin shell. He wants all four new features: screens watching each board (named TVs, reload all), templates for new boards, places (boards grouped by where they hang), and pages per board that rotate, follow a time schedule, or follow ProPresenter. Editors are both admins on laptops and volunteers on tablets, so the editor must work with mouse and touch.

## Tried and ruled out
A (gallery cards plus a full-screen editor in a new tab) and C (zone-based editor instead of the free grid): not chosen. C's places and templates were kept as features inside B.

## Watch out
"Follow ProPresenter" was asked for as following a ProPresenter "slug"; the mockup lets a rule match a presentation, a slide group or a slide label. Confirm which Justin means before building the matcher.

In the code: mockups in docs/mockups/boards-redesign/ (b2-shell.html is the chosen one; it loads app/static/admin.css so the real shell draws). Serve from the repo root with the mockup-planning-center preview entry (port 8470). Current Boards code: app/boards.py, app/static/boards.js, board.js, layout-editor.js, the Boards page in app/templates/admin.html.
