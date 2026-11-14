---
title: Intercom channels have standing members; a production is only a label
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 3488 }
id: l-b89133ba
scope: repo
status: active
replaces: l-2a320952
src: agent
---

## Decided
Justin (2026-11-14, epic 237) chose standing channel membership: intercom channels are permanent and users are added to them with Talk and/or Listen rights, set once and left alone from service to service. A user on several channels picks which to talk and listen on from the phone. Talk priority is set per user per channel and only matters on Pre-empt and One-at-a-time channels; each channel picks one of three modes (pre-empt, party line, one at a time with a priority queue).
He chose to make a production a display label for phones and the log (optionally named from that day's Planning Center plan), with no effect on channel membership, because the crew on each channel rarely changes.
The rest of the earlier design stands: user-based phones with a per-device credential that silently rotates every 30 days, no Redis (live state in asyncio, durable state in SQLite), single tenant with a company_id column, web crew page first with a native app later, remote crew through a separate intercom-remote module on Cloudflare Realtime with a per-user "remote allowed" flag, secrets and backups encrypted, watchdog for LiveKit, the app, the database, Cloudflare and audio loss/jitter (no Wi-Fi AP checks).

## Tried and ruled out
- Per-production routing (each production holding its own copy of membership): Justin wants membership set once.
- Productions adding temporary members for an event: not chosen; label only.
- One global priority per user: per channel lets the director outrank people on Main without outranking them everywhere.
- Redis (extra binary to ship), multi-tenancy (one organisation per server), position-based phones (identity follows the user), whole-file SQLCipher (native builds on three platforms), Wi-Fi AP checks (dropped by Justin).

## Watch out
- iOS Safari is weak at background audio and lock-screen PTT; that is why a native app is planned to join later.

In the code: mockups in docs/mockups/intercom/; the routing screen is admin-intercom.html#members.
