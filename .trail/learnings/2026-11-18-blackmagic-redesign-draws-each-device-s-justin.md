---
title: "Blackmagic redesign draws each device's front and back from a model library: schematic look, clickable front, back shows signal, labels, hover details and oper…"
author: Justin
date: 2026-11-18
branch: main
cost: { minutes: 10, tokens: 180214 }
id: l-1005b975
scope: repo
status: active
src: agent
touches:
  - docs/mockups/blackmagic/devices.js
  - docs/mockups/blackmagic/common.css
  - docs/mockups/blackmagic/a-devices.html
  - docs/mockups/blackmagic/b-rack.html
  - docs/mockups/blackmagic/c-flow.html
  - docs/mockups/blackmagic/index.html
---

## Decided
Justin, 2026-11-18, for the Blackmagic page redesign:
- Draw four families front and back: ATEM Mini Pro/Extreme, ATEM Television Studio, HyperDeck Studio, Videohub (12x12 and 20x20).
- Clean schematic SVG in Relay's tokens (works light and dark), close to the real layout.
- Front panel buttons are clickable and mirror live state (program red, preview green, recording, DSK, FTB). Buttons Relay cannot press (audio, transitions, ATEM record/stream, deck jog/prev/next) are drawn dimmed with a reason on hover.
- Back panel connectors show signal present / in use, the label from the device, details on hover (type, format, routing, cable), and an operator note per port.
- Three layouts mocked with the Planning Center tab bar: A one tab per device, B rack elevation with a Front/Back flip, C signal flow (live control surface + wiring with drawn cables). Layout choice still pending.

## Tried and ruled out
- Near-photo realistic drawings: heavier to build and keep up for many models.
- Simple block diagrams: not shaped like the unit, so users can't match them to the hardware.
- State-only front panel (controls elsewhere): the user wants to press the drawn buttons.

## Watch out
- Model layouts are approximations, not traced from the hardware; check against real units before building.
- Relay cannot see cables; option C needs cables entered by the operator or suggested from matching Videohub/ATEM labels and confirmed.
- HyperDeck control is only record, stop and play (routes.py); ATEM has no record/stream control.

In the code: docs/mockups/blackmagic/ (devices.js holds the model specs and drawing code; a-devices.html, b-rack.html, c-flow.html, index.html). Serve from repo root with the mockup-planning-center preview (port 8470).
