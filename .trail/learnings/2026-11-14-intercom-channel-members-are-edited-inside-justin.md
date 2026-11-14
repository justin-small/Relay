---
title: "Intercom channel members are edited inside each channel, not on a separate grid"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 11122 }
id: l-60bcc85c
scope: repo
status: active
replaces: l-b89133ba
src: agent
touches:
  - docs/mockups/intercom/admin-channels.png
  - docs/mockups/intercom/admin-intercom.html
  - docs/mockups/intercom/admin-live.png
  - docs/mockups/intercom/admin-devices.png
  - docs/mockups/intercom/admin-health.png
  - docs/mockups/intercom/admin-remote.png
  - docs/mockups/intercom/crew-phone.html
  - docs/mockups/intercom/crew-phones.png
---

## Decided
Justin (2026-11-14, epic 237) wants a channel's members managed from that channel's Edit panel: name, colour, talk mode, max talkers, lease, then a Members list with Talk/Listen checkboxes and a per-member priority (shown only on Pre-empt and One-at-a-time channels). Membership is standing, set once and left alone; a production is only a display label, set on the Intercom live page, optionally named from the Planning Center plan.

## Tried and ruled out
- A separate users-by-channels grid page: Justin asked for membership under each channel's Edit instead.
- Per-production routing and productions that add temporary members: membership does not change per event.

## Watch out
- The rest of the intercom design (user-based phones, no Redis, single tenant, remote module) is in the earlier intercom learning this one builds on.

In the code: docs/mockups/intercom/admin-intercom.html#channels.
