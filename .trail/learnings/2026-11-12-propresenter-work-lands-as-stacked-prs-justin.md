---
title: "ProPresenter work lands as stacked PRs: core and control first, panel firmware views next, the issue stays open for automation"
author: Justin
date: 2026-11-12
branch: propresenter-138-follower
cost: { minutes: 3, tokens: 54410 }
id: l-15e0a5b5
scope: repo
status: active
src: agent
---

## Decided
- Justin scoped this pass of issue 138 to the core (follower, lyrics stream, stage message, admin connection with discovery, view-only, health) plus the control set (next/previous, clear dock, slide grid with thumbnails, playlists, macros, looks, timers, messages).
- It lands as a series of PRs, each saying "Part of #138", never "Closes". The issue stays open after they merge, for the automation and "further scope" items.
- Panels get new firmware views (lyrics, and timers with amber/red thresholds) instead of reusing a caption slot.
- Testing may read from and write to the live ProPresenter 22 on this Mac.

## Tried and ruled out
- Core only: too small for what Justin wants from this pass.
- Everything in the issue at once (slide-note tags, lobby announcements, ProPresenter driving Services LIVE, slide images): too large to review.
- One PR: harder to review than a series.
- Reusing a caption slot on panels: Justin chose dedicated firmware views.

## Watch out
- Live writes must not touch the church's existing presentations, playlists or libraries. Never call timers/start (it starts every timer) or capture/start (a 4½-minute stall followed it once).

In the code:
- PR 1 branch: propresenter-138-follower (app/propresenter.py, tests/test_propresenter.py)
- ProPresenter test install: 127.0.0.1:50402
