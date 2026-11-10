---
title: Planning Center LIVE writes are tried only on a throwaway plan; the real API does offer a LIVE webhook
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 2, tokens: 7740 }
id: l-c4f71bed
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - app/config.py
  - app/engine.py
  - app/main.py
  - app/pclive.py
  - app/planningcenter.py
  - app/servicefeed.py
  - app/static/admin.js
  - app/templates/admin.html
  - tests/test_planningcenter.py
---

## Decided
- The test token belongs to a real church account, not a sandbox. Next, Previous and Take control move a plan's LIVE state and take control as the token's owner, so they are tried only on a throwaway plan under a test service type made for it. Control is released afterwards.
- A live read-only check confirmed: item `length` is in seconds (the 'during' items add up to the plan's total_length), and the service-type LIVE path works.

## Tried and ruled out
- Probing on a real upcoming plan: it would move that plan's LIVE state and leave the token's owner holding control.
- Building #80 only against the fake: the docs say nothing about the permission the actions need or what they return.

## Watch out
- Webhooks lists `services.v2.events.plan.live.updated`, although the docs we read showed no LIVE webhook. Relay is on the LAN, so it would need a public address to receive one; polling stays the default.
- Service type names repeat on the real account (two "Wednesday PM"), as an earlier learning warned.

In the code: tools/check_planningcenter.py --live (read-only), git-ignored planningcenter-test.env (PCO_APP_ID, PCO_SECRET).
