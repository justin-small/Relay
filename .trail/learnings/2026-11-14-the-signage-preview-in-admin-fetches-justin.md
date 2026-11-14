---
title: "The signage preview in admin fetches media through admin routes, and the player must count an item that is still loading as the one on screen"
author: Justin
date: 2026-11-14
branch: signage-205
cost: { minutes: 18, tokens: 266860 }
id: l-ba04ac6b
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/main.py
  - app/modules/__init__.py
  - app/modules/signage/__init__.py
  - app/modules/signage/live.py
  - app/modules/signage/media.py
  - app/modules/signage/migrations/0001_signage.sql
  - app/modules/signage/routes.py
  - app/modules/signage/sources.py
  - app/modules/signage/static/player.js
---

## Decided
- The admin page's screen and playlist preview runs the same player script, but takes its state from an admin route that rewrites media addresses to admin file routes. The panel is HTTPS and the screen port is plain HTTP, so a frame or media from the screen port would be blocked as mixed content (and the screen port refuses everything but /signage anyway).
- The admin page refreshes itself every 10 seconds only on the Screens tab (for "now playing"); refreshing elsewhere redrew the playlist editor under the person using it.

## Tried and ruled out
- Deciding whether to carry on from the item already on screen: the first state fetch and the first event-stream message both arrive before the first image has loaded, so the second one skipped to item two. The player now tracks the item being shown, loading or not.

## Watch out
- Admin styles such as .pill set display and beat the hidden attribute; the signage page forces [hidden] to display:none inside its own page.
- On plain HTTP the player's offline fallback is the browser cache (content-hashed, immutable media URLs) plus decoded images kept in memory; a reload while Relay is down shows nothing.

In the code: app/modules/signage/routes.py admin_preview, app/modules/signage/static/player.js (showing, load), app/modules/signage/static/signage.js tick, app/modules/signage/templates/admin.html.
