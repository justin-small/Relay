---
title: "The Blackmagic page is built as the rack mockup: Rack, Wiring, Ports and notes, Find and add, with lights that show only what the devices really report"
author: Justin
date: 2026-11-18
branch: blackmagic-rack-redesign
cost: { minutes: 18, tokens: 241704 }
id: l-5eb7e016
scope: repo
status: active
replaces: l-ec47704f
src: agent
touches:
  - app/modules/blackmagic/static/bmdraw.js
  - app/modules/blackmagic/static/blackmagic.css
  - app/modules/blackmagic/templates/blackmagic.html
  - app/modules/blackmagic/static/blackmagic.js
  - tests/test_blackmagic.py
---

## Decided
Justin, 2026-11-18: build mockup B (rack) with C's Wiring tab, then compare the real page to the mockup screen by screen. After his "the tab bar doesn't match", the tab bar is a full-width strip and the title carries an "N of N connected" pill with no description under it, as in the mockup.
- Notes per connector, cables between connectors, rack order, drawing choice and rack/shelf place are saved on the server (not in the browser).
- Cable suggestions come from Videohub output labels that name a switcher input ("ATEM 3", or the input's own name); the operator confirms them.

## Tried and ruled out
- Green "signal present" on every connector, as the mockup drew it: no protocol Relay speaks reports a signal per port. Green now means "in use now" (on program/preview, routed, recording, or the network/power port of a connected device); amber means cabled.
- Lighting DSK ON and FTB from state: Relay does not read whether a key or fade to black is on air, so those buttons press but never light.

## Watch out
- Drawings are approximations; check them against real units.
- The admin page's script URL carries a hash taken at server start: restart the preview after editing the JS.

In the code: app/modules/blackmagic/static/bmdraw.js (model specs, drawing, picking a drawing from the product name), blackmagic.js (tabs), blackmagic.css, templates/blackmagic.html; service.py DRAWINGS/clean_notes/clean_cables; routes /order, /notes, /cables. Mockups in docs/mockups/blackmagic/ (b-rack.html chosen).
