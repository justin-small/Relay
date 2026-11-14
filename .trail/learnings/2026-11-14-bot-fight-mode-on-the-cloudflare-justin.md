---
title: "Bot Fight Mode on the Cloudflare zone can block Relay's own check-ins and updater, and most zone traffic is scanners"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 2, tokens: 81661 }
id: l-0ba6743a
scope: repo
status: active
src: agent
touches:
  - app/modules/remote_access/cloud.py
---

## Decided
Nothing yet (2026-11-14): tightening proposed to Justin, awaiting a yes. Proposal: allow only the API paths on cloud., block scanner paths and non-80/443 ports zone-wide, rate-limit cloud./v1/*, and turn Bot Fight Mode off.

## Watch out
- Bot Fight Mode is on for the zone and cannot be bypassed on the Free plan. It 403s non-browser clients (curl, python-httpx seen). Relay's check-in (Python urllib, User-Agent "Relay") and the Tauri updater (/updates/latest.json) are non-browser clients; they passed on 2026-11-13/14 but could be challenged.
- Traffic is ~6.4k req/day, nearly all .env/.git/key-file probes; cloud. had only 3 real Relay requests a day. Each junk request on cloud. still runs the Worker.
- Free plan: 5 custom WAF rules (1 used: block AS48090), 1 rate-limit rule, 10s period only. clientAsn isn't available in GraphQL analytics on Free.

In the code:
- Traffic: GraphQL httpRequestsAdaptiveGroups for the zone (python needs SSL_CERT_FILE from certifi on this Mac)
- cf bot-management get -z relaychurchsystems.com; cf rulesets list -z relaychurchsystems.com
- app/modules/remote_access/cloud.py (User-Agent "Relay"); relay-cloud src/index.ts routes
