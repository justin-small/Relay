---
title: "There are two relay-cloud checkouts; the realtime proxy already exists on the pushed branch, and deploying the other one overwrote production"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 3, tokens: 24946 }
id: l-f7241c37
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/cloud.py
---

## Decided
The remote intercom's Cloudflare Realtime proxy was already built by an earlier session: justin-small/relay-cloud, branch origin/realtime-proxy (hono, routes under /v1/agent/realtime, secrets CF_REALTIME_APP_ID and CF_REALTIME_APP_SECRET already set on the live Worker, D1 migration 0002_realtime_sessions already applied). Build on that; do not write a second one.

## Tried and ruled out
- A second proxy in ~/Downloads/relay-cloud (the remote-access Worker, /v1/enroll): it duplicated the work, its migration 0003 conflicted with the existing realtime_sessions table, and deploying it replaced the live Worker version. Rolled back to version ec0398e2 with `wrangler rollback`.

## Watch out
Before any `wrangler deploy`, read `wrangler versions list` and the earlier sessions' scratchpads (find / -name 0002_realtime_sessions.sql). The install client (app/modules/intercom_remote/cloud.py) calls /v1/realtime/*, but the pushed proxy serves /v1/agent/realtime/* and the edge allowlist returns 403 for that path until it is added; the paths still have to be matched.

In the code:
- app/modules/intercom_remote/cloud.py: the paths
