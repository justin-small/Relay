---
title: "The No sound? walk is core's, and modules add the steps upstream of Relay's input"
author: Justin
date: 2026-11-21
branch: claude/hungry-hertz-7b663c
cost: { minutes: 5, tokens: 143301 }
id: l-6fdd151b
scope: repo
status: active
src: agent
touches:
  - app/nosound.py
  - docs/modules.md
---

## Decided
Justin, 2026-11-21, issue 263: core owns the walk and the first steps (capturing, sound arriving); each module that knows a link further upstream adds steps through a provided capability, so no module is imported. Shown on the admin Health page and on a crew tab for volunteers. First slice: input level, then Dante device online, subscription and clock lock. The AES67 checks are out (AES67 was removed, #603).

## Tried and ruled out
Putting the whole walk inside the Dante module: it would not exist when Dante is off and would duplicate the input-level check for churches without Dante.

## Watch out
The sound desk mute step is not built yet; it should use the same hook. Not yet tried on real Dante gear.

In the code: app/nosound.py, app/modules/dante/walk.py, capability `nosound.<module id>`, docs/modules.md "Steps for the No sound? walk", crew route /crew/api/nosound.
