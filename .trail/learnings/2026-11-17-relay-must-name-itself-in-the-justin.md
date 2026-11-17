---
title: "Relay must name itself in the User-Agent when calling relay-cloud, because Cloudflare blocks Python's default one"
author: Justin
date: 2026-11-17
branch: oauth-cloud-redirect
cost: { minutes: 14, tokens: 227790 }
id: l-5aa3e75d
scope: repo
status: active
src: agent
touches:
  - app/static/oauth.js
  - app/oauth.py
  - app/modules/youtube/service.py
  - app/modules/youtube/static/youtube.js
  - app/modules/youtube/__init__.py
  - tests/test_youtube.py
  - tests/test_oauth.py
  - app/modules/remote_access/cloud.py
  - docs/reference/oauth-apps.md
---

## Decided
Every request Relay makes through the shared sign-in helper sends `User-Agent: Relay`, as Remote access already did. Found 2026-11-17 while testing the cloud sign-in live: curl reached the new relay-cloud paths, but Relay got a 403 HTML page from Cloudflare's edge, not from the Worker.

## Tried and ruled out
- Blaming the WAF allow-list: it had been updated and curl passed it. `curl -A "Python-urllib/3.10"` reproduced the 403; `-A Relay` got 201.

## Watch out
- Any new Relay code that calls cloud.relaysystems.dev with urllib must set a User-Agent, or it fails only against the real edge (tests with fakes pass).
- New relay-cloud paths are also blocked by the zone's custom rule "cloud.: allow only the Relay API" until they are added to it; the rate-limit rule covers everything under /v1/.
- Applying relay-cloud's D1 migrations remotely also applied 0003 (it had been made by hand); it is IF NOT EXISTS, so harmless.

In the code: app/oauth.py Provider.call; app/modules/remote_access/cloud.py _call; relay-cloud src/oauth.ts, migrations/0004_oauth_sessions.sql.
