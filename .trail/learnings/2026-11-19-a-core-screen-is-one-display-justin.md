---
title: "A core screen is one display; a feature attaches by key, the same name is the same screen, and a screen shows a board or a signage playlist"
author: Justin
date: 2026-11-19
branch: schedules-page-and-relay-timezone
cost: { minutes: 12, tokens: 298462 }
id: l-1fa2b938
scope: repo
status: active
src: agent
touches:
  - app/screens.py
  - app/static/screens.js
  - app/alerts.py
  - app/main.py
---

## Decided
- Issue 527 (2026-11-19), built from mockup A (the live wall) after Justin rejected the first two mockups and asked what the page is for: the answer was "what is on each TV right now, and how do I change it".
- A core `screens` table plus `screen_keys` (older ids: board:<random id>, signage:<slug>, panel:<id>, alert:<name>) so old addresses keep resolving. The same name is the same screen: adopting a key whose name exists joins that screen, and renaming to another screen's name merges them. This is how the lobby TV known by three names becomes one, without a separate merge screen.
- A screen shows a board or a signage playlist: normal, during a named schedule, or held by hand until cleared. With Signage off a playlist falls back to the board it last showed. The board poll answers `goto`, and the signage state carries `board`, so a browser follows its screen across the two pages; a link from a screen's own address uses `?screen=screen-<id>` so its identity survives the move (a browser's localStorage id differs per origin).
- Alert buttons target a place by putting its ref (`place:relay:<id>`) in the button's `screens` list; `alerts.targets_of` expands it to the panels and browsers in the place. Panels are screens of kind panel, named as the Panels page names them.
- `signage_screens` stays as a signage-only table (address, sound, default playlist) and is baselined in the shared-concepts test; its name and place are the core screen's.

## Tried and ruled out
- Mockup A as a table with a side editor, and B grouped by place with Flash all: both rejected as awful before the purpose was clear.
- Serving boards on the signage port so a TV can switch without a redirect: that port answers only /signage, so the signage player instead sends the browser to the viewer port, as it already does for live views.

## Watch out
- Core may not name a module's routes (test_modules): the signage address comes from `signage.store.player_path`, reached through `signage.content`.
- Not tried on a real TV or panel: the switch between a board and signage was tested through the routes, not in a smart-TV browser. A private board cannot be shown by a signage-to-board switch, because a TV has no sign-in.
In the code: app/screens.py, app/migrations/0008_screens_places.sql, app/static/screens.js, tests/test_screens.py.
