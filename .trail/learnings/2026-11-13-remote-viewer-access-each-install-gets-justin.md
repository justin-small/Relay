---
title: "Remote viewer access: each install gets its own Cloudflare tunnel and random subdomain from a Worker, kept while unused for up to 90 days"
author: Justin
date: 2026-11-13
branch: remote-access-50
cost: { minutes: 23, tokens: 305797 }
id: l-d4b8cf3c
scope: repo
status: active
replaces: l-0d9a32fd
src: agent
touches:
  - app/modules/remote_access/cloud.py
  - app/modules/remote_access/tunnel.py
  - app/modules/remote_access/service.py
  - app/modules/remote_access/routes.py
  - app/modules/remote_access/__init__.py
  - app/modules/remote_access/templates/admin.html
  - app/modules/remote_access/static/remoteaccess.js
  - tests/test_remote_access.py
  - tools/fetch_cloudflared.py
  - docs/guide/src/35-remote-access.md
---

## Decided
Justin (2026-11-13, issue 50): a switchable module. Enrolling with a one-time code asks the relay-cloud Worker (separate repo; it alone holds the Cloudflare API token) to make a tunnel to the viewer port only and <slug>.relaychurchsystems.com. Switching the module off keeps the address. A daily cron releases installs not seen for 90 days and deletes relay-install-* tunnels and "Relay install" CNAMEs no install owns, so Relay checks in every 12 hours while running. Viewer /static/ is cached at Cloudflare's edge; the Worker purges a host's /static/ when its Relay version changes.

## Tried and ruled out
- Quick tunnels: they buffer text/event-stream, so captions never arrive.
- A Worker + Durable Object caption relay: transcripts would pass through our cloud.

## Watch out
- cloudflared's release-notes SHA-256 for a macOS .tgz is of the binary inside, not the archive; both are pinned.
- The zone is Free and new (200 DNS records). Justin will pay for more when needed.

In the code: app/modules/remote_access/ (service.py CHECK_IN_EVERY_S), tools/fetch_cloudflared.py, .github/workflows/release.yml; relay-cloud: src/cleanup.ts, scripts/codes.mjs.
