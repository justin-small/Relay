---
title: "NDI page is redesigned as In and out tabs: Overview, Coming in, Screens, Going out, Setup, with a click-grid router and drag-and-drop multiviews"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 7, tokens: 123165 }
id: l-6fae6861
scope: repo
status: active
src: agent
touches:
  - docs/mockups/ndi/ndi.css
  - docs/mockups/ndi/a-tabs.html
  - docs/mockups/ndi/b-flow.html
  - docs/mockups/ndi/c-sources.html
  - docs/mockups/ndi/index.html
  - app/modules/ndi/templates/ndi.html
---

## Decided
Justin, 2026-11-18: the old NDI admin page (seven stacked sections) was confusing. Of three mockups (A one tab per feature, B in and out, C source wall) he chose B: Overview (in -> screens -> out counts, setup checklist, "needs a look"), Coming in (sources with camera number for tally), Screens (multiviews, single-source watch links), Going out (router + Relay pages as NDI), Setup (discovery, browser picture limits).
From C he wants two pieces in B: the router as a click grid (feeds x sources, like a video matrix) instead of a dropdown per TV, and the multiview editor as drag-and-drop from a palette of sources, clock and countdowns onto boxes.
He uses all three jobs: seeing camera pictures, switching TVs, sending captions out.
In the code: mockups in docs/mockups/ndi/ (b-flow.html chosen; shares ../planning-center/common.css plus ndi.css). Real page is app/modules/ndi/templates/ndi.html with static/ndi.js and ndi-router.js.

## Tried and ruled out
A (one tab per feature) and C (picture wall with side panel) as whole layouts: not chosen.

## Watch out
Serve mockups over http from the repo root (preview entry mockup-planning-center, port 8470), not file://.
