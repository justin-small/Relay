---
title: "Micboard rows: up to 8 mics in one row, 9 to 19 in two, 20 or more in three, all evenly spaced"
author: Justin
date: 2026-11-14
branch: pco-rundown-148
id: l-a7695b0d
scope: repo
status: active
src: agent
touches:
  - app/static/rundown.js
  - app/static/rundown.css
  - app/modules/planning_center/rundown.py
---

## Decided
Justin, 2026-11-14 (issue 148): the micboard and the "On the platform" strip lay mics out in rows. Up to 8 mics use one row, 9 to 19 use two, 20 or more use three. Every column is the same width and the same distance apart. Initials scale with their column, and a long name shrinks to fit rather than being cut off. A position may have up to 24 mics, because a choir needs more than 8.

## Tried and ruled out
- Extra gaps between groups of mics: the gap went in only where both the team and the position changed, so the spacing looked random. Justin asked why it was off. Dropped.
- One ever-thinner row: at 16 or more mics, names and initials no longer fit.

## Watch out
- There is no fourth row. Far past 30 mics the columns get narrow again.

In the code:
- app/static/rundown.js rowsFor(), grid(), fitNames(); app/static/rundown.css (.ch .face container-type, .ini sized in cqw/cqh)
- app/modules/planning_center/rundown.py MAX_MICS = 24
