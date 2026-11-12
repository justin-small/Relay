---
title: "ProPresenter control: the stream misses timer and layer changes too, transport is PUT time, and the live API differs from the description"
author: Justin
date: 2026-11-12
branch: propresenter-138-control
cost: { minutes: 11, tokens: 25441 }
id: l-1558a4a3
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - ".github/workflows/release.yml"
  - README.md
  - app/alerts.py
  - app/config.py
  - app/propresenter.py
  - app/static/admin.js
  - app/static/lyrics.js
  - app/static/viewer.css
  - app/templates/admin.html
---

## Decided
- Stage 2 control lives in app/ppcontrol.py and app/static/ppcontrol.js (admin page ProPresenter control plus a clear dock on every admin page). All writes go through propresenter.Client; Client.action refuses the bulk timers/start|stop|reset and capture/start for good.
- Macros: macros_mode (selected|all) and macros_allowed in the propresenter config; the server checks on every trigger, not only the UI.
- Thumbnails are proxied and cached in memory by (presentation uuid, index, hash of the slides), behind the admin login.

## Watch out (found live on ProPresenter 22)
- The status/updates stream also misses timers/current changes (a stopped timer kept reading running) and may miss layers, so the follower re-reads timers/current, status/layers, playlist/active and look/current every second with the slide.
- The issue says transport/presentation/go_to_time; the published description and the live install use PUT /v1/transport/{layer}/time with a bare number. PUT 0 returns 204 even with no media.
- GET /v1/playlists answers field_type and children, not type and playlists as the description says. Cue indexes follow the current arrangement (arrangements[].groups lists group uuids, so a repeated chorus has its own cue); the plain groups list does not.
- POST /v1/timers takes name flat (not id.name); POST /v1/messages needs theme and is_active. A timer created through the API answered DELETE 204 once, then 404 on every later try while still listed: delete 'ZZ relay test timer' by hand if it is still in ProPresenter.
- A path-shaped id such as ../clear/layer/slide is refused by the control routes (ID_RE) before any request is built.
