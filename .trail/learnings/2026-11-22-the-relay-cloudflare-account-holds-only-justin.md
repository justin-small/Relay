---
title: "The Relay Cloudflare account holds only two Workers, one D1 database, one Realtime app and two zones"
author: Justin
date: 2026-11-22
branch: issue-629-core-transports
cost: { minutes: 1, tokens: 43791 }
id: l-24f868cc
scope: repo
status: active
src: agent
touches:
  - site/cloudflare.config.ts
  - app/modules/remote_access/cloud.py
  - app/modules/intercom_remote/cloud.py
---

## Decided
Inventory taken live from the account on 2026-11-22. Workers: relay-cloud (cloud API) and relay-site (marketing site). D1: relay-cloud. Realtime SFU app: relay-intercom. Zones: relaysystems.dev (current) and relaychurchsystems.com (old, WAF rules left behind). Nothing else is in use: no KV, R2, Queues, Pages, Durable Objects or named tunnels.

## Watch out
- The relay-cloud Worker source is not in this repo, so its bindings can't be read here; query the account instead (Cloudflare MCP, GET /accounts/<id>/workers/scripts, /d1/database, /calls/apps).
- Zone rules are per zone, see the allow-list learning.

In the code: site/cloudflare.config.ts (relay-site), app/modules/remote_access/cloud.py and app/modules/intercom_remote/cloud.py (relay-cloud clients). Account id 866098ef8557cbeb6b496659155d9d98.
