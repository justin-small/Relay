---
title: A Planning Center position can use several input slots and each person is drawn one at random; avoid the word group
author: Justin
date: 2026-11-20
branch: main
cost: { minutes: 1, tokens: 10616 }
id: l-22e03845
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/static/planningcenter.js
  - app/modules/planning_center/pclive.py
---

## Decided
Justin, 2026-11-19, issue 546 mockup: a position maps to one or more operator-named values, and each person on the position is drawn one of them at random (kept per plan and service time, as in the 2026-11-14 rule). Each value then maps to one or more input channels. Do not call the value a "group": it reads as grouping things together. The mockup calls it an "input slot" as a placeholder name.

## Tried and ruled out
- One group per position: a position needs several values to draw from.
- "Group" as the word: misleading.

## Watch out
The name "slot" is a placeholder; Justin has not confirmed it. The draw should avoid giving two people the same slot while a free one exists (assumed, not confirmed).

In the code: docs/mockups/shared-pickers/common.js (pco page); build in app/modules/planning_center/static/planningcenter.js and app/modules/planning_center/pclive.py.
