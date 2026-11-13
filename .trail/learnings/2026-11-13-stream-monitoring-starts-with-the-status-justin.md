---
title: "Stream monitoring starts with the status view, then OBS, and the OBS module owns the one connection the caption sender will share"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 3, tokens: 68682 }
id: l-033a63aa
scope: repo
status: active
src: agent
touches:
  - requirements.txt
---

## Decided
- Justin (2026-11-13, epic 249): the work goes in this order. First the panel and browser status view (224), together with a shared stream-status state that modules publish into. Then the OBS module (250). The other providers (Resi, YouTube/Facebook, BoxCast/Restream, GA4/Church Online) come later and plug into the same state.
- The OBS module owns the single obs-websocket connection and exposes a call hook, so the OBS caption sender in 140 reuses it instead of opening a second connection.
- Live testing is possible against OBS Studio (websocket 5) only; every other provider is built against mocks.

## Tried and ruled out
- Doing the whole epic in one go: too broad. Six sub-issues, one after another.
- Publishing stream status with no on-screen view until 224 lands: Justin chose to build 224 here so the state has a place to show.
- An OBS module that only monitors, to be refactored when 140 arrives: dropped, because 140 already says it must share the connection.

## Watch out
- Tally is not in this work (242/246).
- The OBS module, like every integration, stays in app/modules/; the shared stream-status state is core, so modules write into it and core never imports them.

In the code: planned app/streamstatus.py (core, add to CORE_FILES), /stream?stream=status, firmware feed kind kFeedStatus 7, app/modules/obs/ using the websockets library already in requirements.txt.
