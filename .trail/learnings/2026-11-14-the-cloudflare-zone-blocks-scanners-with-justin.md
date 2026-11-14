---
title: "The Cloudflare zone blocks scanners with WAF rules instead of Bot Fight Mode, so Relay's own check-ins and updater are never challenged"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 12076 }
id: l-492996b9
scope: repo
status: active
replaces: l-0ba6743a
src: agent
touches:
  - app/modules/remote_access/cloud.py
---

## Decided
Justin (2026-11-14): Bot Fight Mode is off. Most zone traffic (~6.4k req/day) was scanners probing .env, .git and key files. Bot Fight Mode can't be bypassed on the Free plan and was 403ing non-browser clients like curl and python-httpx, and Relay's check-in (Python urllib, User-Agent "Relay") and the Tauri updater are non-browser clients. Replaced with:
- cloud.: block everything except POST /v1/enroll, PUT/DELETE /v1/install, GET /health (scanners never reach the Worker).
- Zone-wide: block dotfile paths (except /.well-known/), .php, wp-, phpinfo, .yml/.yaml/.sql/.bak/.ini. This covers the <slug>. viewer hosts too.
- Zone-wide: block ports other than 80 and 443.
- Rate limit: cloud./v1/* over 10 requests in 10s per IP is blocked for 10s.

## Watch out
- A new relay-cloud endpoint, or a new method on an existing one, is blocked at the edge until it's added to the cloud. allowlist rule.
- A Relay viewer route or marketing-site file that starts with a dot or ends in .yml/.php etc. would be blocked by the scanner-path rule.
- Free plan: 5 custom rules (4 used), 1 rate-limit rule (used), 10s period only. clientAsn isn't in GraphQL analytics on Free.
- AI crawler blocking was left off on purpose, so AI search can still see the marketing page.

In the code:
- cf rulesets list -z relaychurchsystems.com (custom ruleset 30f11dd83b2f49cd91a55c17e62d3e55; http_ratelimit ruleset created 2026-11-14)
- cf bot-management get -z relaychurchsystems.com (fight_mode false)
- app/modules/remote_access/cloud.py (User-Agent "Relay"); relay-cloud src/index.ts routes; site/public/_redirects (/updates/latest.json)
