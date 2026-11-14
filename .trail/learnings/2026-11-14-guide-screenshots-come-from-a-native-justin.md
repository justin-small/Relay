---
title: "Guide screenshots come from a native rehearsal Relay, and the capture script scrubs this machine's address, path and ports"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 3575 }
id: l-f72e7690
scope: repo
status: active
replaces: l-54ae28f0
src: agent
touches:
  - docs/guide/tools/capture.mjs
  - docs/guide/README.md
---

## Decided
The guide's screenshots are retaken from a throwaway Relay run from the checkout: RELAY_HOME at an empty 0700 folder holding a config.json with a fake key, an admin token, host/admin_host 127.0.0.1, ports 18080/18443 and the Planning Center module switched on (its page is captured; a fresh state folder has every module off, and capture.mjs then times out waiting for #page-planningcenter). RELAY_DEMO=1 and RELAY_ANNOUNCE=0, so it costs nothing and caption panels never find it.

## Tried and ruled out
Relying on the old Docker trick, where the container only knew 127.0.0.1: a native instance reads this Mac's real interfaces, so the Viewer links page showed the real LAN address and the Recordings/Blocked words pages showed the scratch path with the user name in it. capture.mjs now rewrites every JSON answer from /api/admin/** (not the endless /stream ones): real LAN addresses to 192.168.1.50, the state folder (and its realpath, /tmp is /private/tmp on macOS) to ~/Library/Application Support/Relay, and the throwaway ports to none.

## Watch out
Still look at admin-viewer-links, admin-recordings and admin-blocked-words before committing new images. Panels, Alerts and Planning Center must skip the status override (it drops the panels frame). The two flashing screenshots are taken by hand and not retaken.

In the code: docs/guide/tools/capture.mjs (scrub, REAL_IPS, STATE_PATHS, adminPage routes), docs/guide/README.md (Retaking the Relay screenshots).
