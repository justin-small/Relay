---
title: "Remote intercom reaches Cloudflare Realtime only through relay-cloud, which holds the app secret and is called directly, not through the tunnel"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 6, tokens: 55585 }
id: l-de05191a
scope: repo
status: active
src: agent
touches:
  - app/modules/remote_access/cloud.py
---

## Decided
Justin (2026-11-15, issue 277): the install never holds the Cloudflare Realtime app secret. The existing relay-cloud Worker (same one used for enrollment and check-in, called straight over outbound HTTPS, not through the tunnel) proxies Realtime session calls, and an install can only act on sessions it opened. relay-cloud is built first, then the Relay side of 277, then 276. A Realtime SFU app "relay-intercom" exists and its ID and secret are Worker secrets on the live relay-cloud.

## Tried and ruled out
- Handing installs short-lived Cloudflare credentials: Realtime has no token minting, only the app secret, so a proxy is the only way to keep the secret off the install.
- Systemd watchdog or desktop restart in the 276 health work: dropped, only LiveKit is ever restarted.

## Watch out
The deployed relay-cloud was not deployed from the repo's config (placeholder database IDs and domains), so deploy by matching the live Worker. New routes under /v1/agent/realtime must be added to the cloud. allowlist rule by hand or the edge blocks them.

In the code:
- relay-cloud repo, branch realtime-proxy: src/routes/realtime.ts, migrations/0002_realtime_sessions.sql
- Install-side client calls go next to app/modules/remote_access/cloud.py
