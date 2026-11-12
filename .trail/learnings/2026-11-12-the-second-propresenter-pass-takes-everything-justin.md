---
title: "The second ProPresenter pass takes everything left in issue 138 as four more stacked PRs; slide-note tags use [relay:...] and Services LIVE waits 10 seconds"
author: Justin
date: 2026-11-12
branch: main
cost: { minutes: 2, tokens: 53611 }
id: l-6f30d9ae
scope: repo
status: active
src: agent
---

## Decided
- Justin scoped the second pass of issue 138 to all remaining items, as four stacked PRs, each "Part of #138": (A) finish live state (announcements, status/screens, playlist thumbnails and section markers, item details, keyboard playlist next/previous); (B) slide-note tags; (C) Planning Center link (setlist swap, follow the plan, ProPresenter drives Services LIVE); (D) lobby announcements and slide images with overlays.
- Slide-note tag syntax is [relay:action] or [relay:action=value] per slide, and [relay-deck:...] for deck-wide defaults that apply to untagged slides.
- ProPresenter drives Services LIVE is off by default and advances only after a presentation has been active 10 seconds (configurable), and only while Relay's token already holds LIVE control.
- Live testing against the ProPresenter 22 on this Mac is allowed again under the same rules.

## Tried and ruled out
- #hashtags in notes: more likely to clash with real note text.
- 5 s or 30 s default dwell: 10 s ignores a mis-click and still keeps LIVE in step.

## Watch out
- Same guard rails as the first pass: never touch the church's existing presentations, playlists or libraries; never call timers/start or capture/start.

In the code:
- Branches: propresenter-138-state, propresenter-138-tags, propresenter-138-pclink, propresenter-138-lobby (each on the previous)
