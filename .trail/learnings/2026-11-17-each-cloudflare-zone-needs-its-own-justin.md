---
title: "Each Cloudflare zone needs its own copy of the API allow-list, scanner blocks, rate limit and viewer cache rule"
author: Justin
date: 2026-11-17
branch: cloud-domain-relaysystems-dev
cost: { minutes: 1, tokens: 1675 }
id: l-1b96903b
scope: repo
status: active
src: agent
touches:
  - app/modules/remote_access/cloud.py
  - app/modules/intercom_remote/cloud.py
---

## Decided
Justin, 2026-11-16: when the cloud moved to relaysystems.dev, the old zone's custom WAF rules, rate limit and viewer static cache rule were copied to the new zone with the hostnames changed. Zone rules do not follow a Worker or a domain move; the new zone had none, so the cloud API was open to scanners and unthrottled.

## Watch out
- The allow-list matches http.host eq the API host exactly, so viewer hosts one level below it (<slug>.cloud.relaysystems.dev) are not blocked by it. Keep it an exact match.
- A new call Relay makes to relay-cloud must be added to the allow-list on every zone that serves the API, or it answers 403 at the edge.
- The old zone (relaychurchsystems.com) still has its copies; cloud.relaychurchsystems.com is no longer attached to the Worker.

In the code: app/modules/remote_access/cloud.py (DEFAULT_URL, the calls the allow-list must permit), app/modules/intercom_remote/cloud.py (/v1/realtime/*). Zone 36cd9f9e1a7ccce684d583d9c4765ee4, phases http_request_firewall_custom, http_ratelimit (100 per 10 s per IP and colo), http_request_cache_settings (ends_with host ".cloud.relaysystems.dev", /static/).
