---
title: "Lighting cue pages on /screen: one key per page, a look per button, editor split to a core issue"
author: Justin
date: 2026-11-16
branch: lighting-lightkey-osc
cost: { minutes: 7, tokens: 158568 }
id: l-abe12a14
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - tests/test_auth.py
  - app/modules/lighting/routes.py
---

## Decided
Issue 379, 2026-11-16, Justin's choices: build #379 now and file the shared editor separately (#421); each page has its own on/off/rotate key (128-bit hex) and picks its own cues; buttons get label text, shape, colours, grid size and free drag/resize. Core serves /screen?lighting= through the generic modules.screen_pages hook, never importing the module.
## Tried and ruled out
One key for all pages: a leaked link would open every page.
## Watch out
The look form and preview live in the lighting admin page until #421 replaces them. The key is returned only by the manage-only link route; a page save never changes keys. Not tried on a real tablet or browser (Playwright not installed locally).
In the code: app/modules/lighting/routes.py (screen_page, screen_press), service.py (clean_style, screen_page), static/lighting-buttons.js (shared drawing).
