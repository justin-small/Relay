---
title: "Blackmagic redesign: the rack layout with a Wiring tab, drawing each device's front and back from a model library"
author: Justin
date: 2026-11-18
branch: main
cost: { minutes: 1, tokens: 11648 }
id: l-ec47704f
scope: repo
status: active
replaces: l-1005b975
src: agent
touches:
  - docs/mockups/blackmagic/b-rack.html
  - docs/mockups/blackmagic/index.html
---

## Decided
Justin, 2026-11-18: of three Blackmagic mockups he chose B (the rack) and asked for C's Wiring tab to be added to it. Tabs: Rack, Wiring, Ports and notes, Find and add.
- Draw ATEM Mini Pro/Extreme, ATEM Television Studio, HyperDeck Studio and Videohub (12x12, 20x20) front and back as clean schematic SVG in Relay's tokens.
- Front buttons are clickable and mirror live state; buttons Relay cannot press are dimmed with a reason.
- Back connectors show signal, the device's label, hover details and an operator note.

## Tried and ruled out
- A (one tab per device) and C (signal flow as its own page): not chosen, but C's wiring view lives on as B's Wiring tab.
- Near-photo realistic or block-diagram drawings; a front panel that only shows state.

## Watch out
- Model layouts are approximations; check against real units.
- Relay cannot see cables: they are entered by the operator or suggested from matching labels and confirmed.
- HyperDeck control is only record, stop and play; ATEM has no record/stream control from Relay.

In the code: docs/mockups/blackmagic/b-rack.html (chosen), devices.js (model specs and drawing). Serve with the mockup-planning-center preview (port 8470).
