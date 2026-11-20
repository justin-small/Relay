---
title: "The remote phone page waits for its own Cloudflare connection before pulling, asks for the mic before opening a session, and is named as a microphone page"
author: Justin
date: 2026-11-15
branch: intercom-remote-277-media
cost: { minutes: 8, tokens: 142077 }
id: l-7a4ba691
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/static/phone.js
  - app/modules/intercom_remote/remote.py
  - app/modules/intercom_remote/__init__.py
---

## Decided
Issue 277, 2026-11-15. Three things stopped the remote phone page from connecting audio, found by running the real page in headless Chrome (fake mic) through the tunnel against live Cloudflare:
1. The page sent cf.pull straight after the push answer, before its own connection was up. Cloudflare hangs on a pull into an unconnected session, so relay-cloud answered 502 (upstream_unreachable) after 10 s. The scripted aiortc client worked only because it waited for "connected". The page now waits (15 s max), and Relay refuses such a pull at once without calling the cloud.
2. The page opened its Cloudflare session before the microphone prompt; a slow answer to the prompt let Cloudflare drop the unused session. The mic is now asked for first.
3. The module never named /intercom/remote/ in microphone_pages, so Relay sent microphone=() and Chrome refused the mic ("Permission denied"). iOS Chrome (WebKit) ignored it, which hid this.

## Watch out
After these fixes headless desktop Chrome connects in 5-7 s and receives both mixes. An iPhone (Chrome on iOS, T-Mobile IPv6 cellular) pushed its offer but never reported its connection up, so its problem is the connection itself, not the pull; not yet diagnosed. The bridge's own candidates from Cloudflare were IPv4 only (141.101.90.0). Each test run costs about 6 relay-cloud calls of the ~30 a minute allowed, and a phone testing at the same time shares that budget.

In the code:
- app/modules/intercom_remote/static/phone.js: audio(), connected()
- app/modules/intercom_remote/remote.py: State.pushed / State.up guard on cf.pull
- app/modules/intercom_remote/__init__.py: microphone_pages
