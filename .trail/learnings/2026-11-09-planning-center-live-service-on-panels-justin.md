---
title: "Planning Center live service on panels: token sign-in, polling, and control chosen per panel"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 1, tokens: 2050 }
id: l-5249d151
scope: repo
status: active
src: agent
touches:
  - app/config.py
  - app/main.py
  - app/hub.py
---

## Decided
- Sign in to Planning Center with a Personal Access Token pasted into admin, because Relay serves one church and a token needs no OAuth app or refresh. Every action runs as the token's owner.
- The live service is its own "Service" feed in the panel menu, not a banner over captions, so captions keep the whole screen.
- Whether a panel shows the service, controls it (Next / Previous / Take control), or both is the operator's choice per panel in admin, because some panels sit where the audience can touch them.
- Relay makes every Planning Center call; panels never hold the token.

## Tried and ruled out
- OAuth sign-in: only needed for many churches, which is out of scope.
- A banner on caption feeds: dropped in favour of a separate feed.

## Watch out
- No push for Services LIVE was confirmed, so Relay polls, fast only near service time. Read the rate limit from X-PCO-API-Request-Rate-* headers rather than hard-coding it.
- Send X-PCO-API-Version: 2018-11-01 and a User-Agent, or requests can get 403.
- ItemTime uses live_start_at/live_end_at; PlanTime uses live_starts_at/live_ends_at. Planning Center gives no time remaining, so Relay works it out.
- Panel control rides plain HTTP, so keep it opt-in per panel, keyed, rate limited and logged. Item notes stay off panels unless the operator allows them.

In the code: the token belongs in app/config.py beside the OpenAI key and must stay out of the /api/admin/state response in app/main.py; the Service feed joins /stream in app/main.py and app/hub.py. Tracked in GitHub epic justin-small/Relay#85.
