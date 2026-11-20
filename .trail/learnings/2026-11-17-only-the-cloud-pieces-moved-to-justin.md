---
title: "Only the cloud pieces moved to relaysystems.dev; the marketing site, updater and OAuth pages stay on relaychurchsystems.com"
author: Justin
date: 2026-11-17
cost: { minutes: 4, tokens: 64362 }
id: l-5c8b768d
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/prefs.py
  - app/static/admin.css
  - app/static/admin.js
  - app/static/look.js
  - app/static/shell.js
  - app/static/standalone.css
  - app/static/tokens.css
  - app/static/users.js
  - app/static/viewer.css
---

## Decided
Justin, 2026-11-16: the cloud (relay-cloud API and the per-install viewer addresses) moved to relaysystems.dev. relay-site, and everything that depends on it, stays on relaychurchsystems.com.

## Tried and ruled out
Replacing the domain everywhere, including the marketing site, the desktop updater URL and the OAuth privacy-policy notes, and attaching relaysystems.dev to relay-site. Justin said relay-site is not to change, so it was backed out.

## Watch out
- relay-cloud's ZONE_NAME and CF_ZONE_ID were patched in place (secrets kept with type "inherit", multipart PATCH). Its API token must be allowed on the new zone; not tested with a real enrollment.
- cloud.relaychurchsystems.com is still attached to relay-cloud for already-enrolled installs. site/public/privacy.html still names it.

In the code: app/modules/remote_access/cloud.py (DEFAULT_URL), desktop/src-tauri/src/updater.rs and tauri.conf.json (updater stays on the old domain).
