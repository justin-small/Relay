---
title: "Planning Center positions map to operator-named input groups, which map to input channels; the church's own examples are not the design"
author: Justin
date: 2026-11-20
branch: main
cost: { minutes: 1, tokens: 1436 }
id: l-f3f48b4c
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/static/planningcenter.js
  - app/modules/planning_center/pclive.py
---

## Decided
Justin, 2026-11-19, issue 546 mockup: a Planning Center position (many people each) is linked to one intermediate value, an "input group" the operator names, and a group is mapped to one or more input channels. Text says "Input channel", not "mic channel". Wording stays generic: no colours, no church terms (tenor, alto, worship leader were examples of positions, not fixed values). Groups are operator-made: add, rename, delete.

## Tried and ruled out
- Person linked straight to a mic channel: wrong, there is an intermediate value.
- Colour-coded groups ("mic colour", then fixed Group 1 to 6 with colour tags): Justin said his colour example only illustrated how it works, so colours are out.

## Watch out
Treat a user's example from their own church as an example of the mechanism, not a requirement. Open: when a group has several input channels, each person is given one (per the 2026-11-14 learning); not yet confirmed against sharing all of them.

In the code: app/modules/planning_center/static/planningcenter.js (Team and mics tab) and app/modules/planning_center/pclive.py (position to mic map); mockup in docs/mockups/shared-pickers/common.js; chosen picker style is option B.
