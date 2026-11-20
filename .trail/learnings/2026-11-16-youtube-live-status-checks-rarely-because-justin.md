---
title: "YouTube live status checks rarely because every install shares one daily quota, and speeds up when any stream is on air"
author: Justin
date: 2026-11-16
branch: youtube-live-status
id: l-f39d3daa
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/oauth.py
  - app/static/oauth.js
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
- Issue 252, 2026-11-16, built on Justin's go-ahead with no further questions. Every Relay install shares one YouTube Data API allowance (10,000 units a day per Google project), so the module checks every 60 s while YouTube or any other stream-status entry (OBS, say) is on air, and every 600 s otherwise. Both are settings (30–600 s and 120–3600 s).
- A check is liveBroadcasts.list with broadcastStatus=active (1 unit), plus videos.list for concurrentViewers (1 unit) only while a broadcast is live.
- On quotaExceeded, Relay stops asking until 00:05 Pacific (computed as PST, so under PDT it waits an hour longer).
- Read-only scope only (youtube.readonly). The public status-view label is just "YouTube"; broadcast titles stay in the admin-only detail.
- Until Relay's own Google app exists, BUILTIN client is empty and a church enters its own "TVs and Limited Input devices" client ID and secret (bring your own app).

## Watch out
- Not run against real Google; tested against a fake provider and a local fake server. The first real test needs a Google Cloud client.
- Module defaults must match service.DEFAULTS (a test checks).

In the code: app/modules/youtube/service.py (Google provider, Service.next_wait, _refused, _quota_reset), oauth.Account(on_change=...) pokes the check after connecting.
