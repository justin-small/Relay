---
title: "Signage web pages and YouTube are saved links in a module of their own; the player checks the internet and listens for YouTube's end"
author: Justin
date: 2026-11-14
branch: signage-web-youtube-210
cost: { minutes: 10, tokens: 154490 }
id: l-fbd09946
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/modules/planning_center/__init__.py
  - app/modules/planning_center/planningcenter.py
  - app/modules/planning_center/routes.py
  - app/modules/planning_center/signagesync.py
  - app/modules/planning_center/static/pcsignage.js
  - app/modules/planning_center/templates/admin.html
  - tests/test_auth.py
  - tests/test_planning_center_signage.py
  - app/modules/signage/static/player.js
---

## Decided
- Justin answered four questions on issue 210 (2026-11-14): web pages show in a full-screen iframe with an admin warning (checked from Relay when a link is added); YouTube takes video and playlist links with an option to use the video's length (duration 0); offline is handled by the player, not Relay.
- Screens already had a Sound setting (#205), so YouTube plays muted unless that is on. The module hands the player two addresses (`url`, `url_sound`) and signage picks.
- The module saves links in its own config key (`signage_web.links`); a playlist item's ref is the link id, so editing an address changes every playlist using it.
- Signage's source contract gained optional page fields: `probe` (skip while unreachable), `url_sound`, `post_message` and `end_when` (a message from the frame ends the item at duration 0). Signage itself stays free of YouTube code.

## Tried and ruled out
- Reading a video's length from YouTube: needs an API key. The embed's own "ended" message is used instead; a playlist has no reliable end, so it runs for the item's seconds.
- Showing a site that refuses framing through a Relay proxy: fragile and a security risk; Relay only warns.
- Relay-side internet probing: it cannot see one screen's own connection.

## Watch out
- A frame appended before its `src` is set fires `load` for about:blank at once; set `src` first. A frame that is not in the page never loads at all (pages used to wait 8 seconds for that), and moving a loaded frame reloads it.
- Not run against real YouTube or a real TV browser: the end-of-video message and the probe were tried with a local stand-in page only.
- The admin check fetches whatever address an admin typed, from Relay's machine.

In the code: app/modules/signage_web/, app/modules/signage/static/player.js (_reachable, _watchEnd), signage/sources.py (resolve), tests/test_signage_web.py.
