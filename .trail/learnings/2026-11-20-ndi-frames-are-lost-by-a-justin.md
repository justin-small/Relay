---
title: "NDI frames are lost by a receive call with a timeout, so analysis health reads wrong until issue 566 is fixed"
author: Justin
date: 2026-11-20
branch: ndi-analysis-300
id: l-d9e0d7e7
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/service.py
  - app/modules/ndi/analysis.py
---

## Decided
Justin, 2026-11-19: merge the Analysis tab (issue 300) knowing its health verdicts and measured frame rate read worse than the stream, and track the fix as issue 566.

## Tried and ruled out
Blaming the sender, the network, the colour format or the frame copy: the sender sent a steady 30.0 fps, the NDI library counted about 151 frames in 5 s with none dropped, and copying a frame costs 0.2 ms. Polling with a timeout of 0 plus a short sleep gave under 1 fps and is not understood.

## Watch out
cyndilib's receive call with a timeout delivers far fewer frames than arrive: about 20 fps at 500 ms (what the pool uses), 9 fps at 20 ms, and 30 fps only when busy-polling at 0 ms, which uses a whole core. Compare frames delivered with get_performance_data()["video"]["frames_total"] before trusting any frame-rate figure. Test with the sender in a separate process.
In the code: app/modules/ndi/service.py (Pool._run, conn.receive(500)), app/modules/ndi/analysis.py (health thresholds).
