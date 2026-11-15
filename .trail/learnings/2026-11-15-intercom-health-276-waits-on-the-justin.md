---
title: "Intercom health (#276) waits on the remote bridge (#277): bridge built first, no systemd watchdog, LiveKit is the only thing restarted"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 1, tokens: 1482 }
id: l-8a80c41d
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/livekit.py
  - app/modules/intercom/live.py
---

## Decided
Justin answered questions on issue 276 (2026-11-15): do #277 (Cloudflare bridge) first as its own PR, then #276 on top so the bridge health check reads real bridge state. Loss is derived as a percentage from packet counts over the 10 s window, replacing the placeholder thresholds. Thresholds are module settings with the issue's defaults. One PR for #276. The Relay-app card (event-loop lag, RSS, sockets) only measures and warns; the watchdog only ever restarts LiveKit. The bridge needs the `livekit` and `aiortc` packages, imported lazily so a missing one shows as a health state. Tests use fakes; the real-audio checkboxes stay unchecked.

## Tried and ruled out
- systemd WatchdogSec with sd_notify, and a desktop shell restart on health timeout: dropped, Justin wants no whole-app restart.
- Stubbing the bridge check until #277 lands: offered, not chosen.
- Splitting #276 into two PRs: offered, not chosen.

## Watch out
Short-lived Cloudflare credentials come from the separate private repo justin-small/relay-cloud (Cloudflare Workers), never from the install, so #277 spans two repos.

In the code:
- LiveKit supervisor and restart back-off: app/modules/intercom/livekit.py
- Placeholder thresholds to replace: LOSS_PACKETS and JITTER_MS in app/modules/intercom/live.py
