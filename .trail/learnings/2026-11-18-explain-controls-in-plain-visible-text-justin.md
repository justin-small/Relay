---
title: "Explain controls in plain visible text under a small box title; never hide the explanation behind a click, and keep pickers compact"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 12, tokens: 52776 }
id: l-682ec95a
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/rundown.py
---

## Decided
Justin, 2026-11-17, on the Planning Center mockups:
- Help text for a control is always visible: a small box title like the other boxes, one line saying what it does, and a short hint under the fields. A collapsed "What is this?" was rejected because nobody clicks it.
- Pickers stay compact: three short dropdowns in one row. A big card with a hint under every field looked awful and its text overran the neighbouring dropdowns.
- Team and mics works from soundboard channels: channels come from the (not yet built) soundboard module, are added to the board from a list, then each channel is assigned to one or more positions, across different service types. "Redraw" was unclear; it is now "Shuffle who gets which channel" with a sentence saying it re-picks who gets which channel when a position has several people.
- The "Lights up" (sings) option is dropped from the mic UI.

## Tried and ruled out
Per-position table with a mics text box, per-position cards, and a collapsed explanation: all rejected as cramped, too big or unclear.

## Watch out
The soundboard module does not exist yet, so the channel list in the mockup is invented. The real page needs that module to supply channel names, and the saved config's per-position mic labels (planning_center.rundown.mics, with its draws) would have to change to channel-to-positions.
In the code: docs/mockups/planning-center/a-tabs.html and common.css; real logic in app/modules/planning_center/rundown.py (draw_mics, clean_rundown) and static/pcrundown.js (renderMics).
