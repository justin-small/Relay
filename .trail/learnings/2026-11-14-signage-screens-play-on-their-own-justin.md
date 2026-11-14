---
title: "Signage screens play on their own plain-HTTP port that answers only signage, opened by a generic module listener, and media is mirrored into backups"
author: Justin
date: 2026-11-14
branch: main
cost: { minutes: 3, tokens: 54090 }
id: l-58bdde73
scope: repo
status: active
src: agent
touches:
  - run.py
  - app/serving.py
  - app/db.py
---

## Decided
Justin, 2026-11-14, issue 205:
- /signage/<slug> is served on a separate plain-HTTP port (default 8090, changeable in signage settings, restart needed), not the viewer port and not HTTPS: TVs cannot trust Relay's self-signed certificate.
- That port answers only signage routes (player page, its script, media, live updates); everything else is 404 there. LAN only: nothing signage goes through the remote access tunnel.
- Core opens it through a generic module listener: a module declares `listeners` (name, default port, route prefixes); run.py opens a plain socket for each one of a module that is on, and core refuses other routes on it. Core never names signage.
- Uploaded media is mirrored into backups/signage-media/, deduplicated by content hash; playlists and screens are in the relay.db snapshot.
- Limits: 25 MB per image, 500 MB per video, no total cap; the storage readout shows used and free disk space.
- Thumbnails are made in the admin browser at upload (canvas, small WebP) and uploaded with the file. No Pillow, no ffmpeg.

## Watch out
On plain HTTP browsers block service workers and the Cache API, so "keeps playing when Relay restarts" means the player keeps looping what it already has loaded in the page until Relay is back; it does not survive a reload while Relay is down.

In the code: app/modules/signage/ (new), run.py and app/serving.py (module listeners), app/db.py backups.
