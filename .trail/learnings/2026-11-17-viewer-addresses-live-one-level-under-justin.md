---
title: "Viewer addresses live one level under cloud.relaysystems.dev, set by relay-cloud's VIEWER_DOMAIN"
author: Justin
date: 2026-11-17
cost: { minutes: 12, tokens: 223693 }
id: l-aa9fd35c
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - app/modules/remote_access/cloud.py
---

## Decided
Justin, 2026-11-16: new installs get <slug>.cloud.relaysystems.dev instead of <slug>.relaysystems.dev. relay-cloud builds the name from a VIEWER_DOMAIN var (wrangler.jsonc), separate from ZONE_NAME, so the zone apex stays free for the redirect to relaychurchsystems.com.

## Watch out
- Two levels deep is not covered by Universal SSL; the zone has an Advanced certificate for cloud.relaysystems.dev and *.cloud.relaysystems.dev. If that certificate lapses, every viewer address fails TLS. Slugs must stay one label.
- The zone is Free, created after 2024-09: 200 DNS records, one per install.
- The redirect rule on relaysystems.dev must stay scoped to the apex and www. Its first version matched everything and 301'd the cloud API.
- Installs enrolled before the change keep the hostname stored in D1 (one, a7g3h559.relaychurchsystems.com, whose DNS record is in the old zone).

In the code: relay-cloud src/index.ts (hostname), src/cf.ts Env, wrangler.jsonc; Relay app/modules/remote_access/cloud.py docstring and tests/test_remote_access.py fixtures.
