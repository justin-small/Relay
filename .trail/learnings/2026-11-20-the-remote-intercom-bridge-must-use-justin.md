---
title: "The remote intercom bridge must use Cloudflare TURN relay-only, and a bridge blip must not tell phones to sign in again"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 50, tokens: 291117 }
id: l-afee102a
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/media.py
  - app/modules/intercom/presence.py
  - app/modules/intercom_remote/remote.py
  - tests/test_intercom_remote.py
  - app/modules/intercom_remote/static/phone.js
  - app/modules/intercom_remote/cloud.py
  - app/modules/intercom_remote/bridge.py
---

## Decided
Justin, 2026-11-20, issue 277: the bridge's Cloudflare connections now require TURN relay candidates whenever a TURN server is offered. On the venue LAN a direct UDP pair to Cloudflare answers ICE but never completes DTLS (20 s timeout, 2 of 2 tries with TURN offered but not required; 0.4 s, 3 of 3 with relay required). aiortc cannot ask for relay-only, so a subclass of aioice's Connection is swapped in once. Only the bridge uses aiortc, and with no TURN server nothing changes.
A remote socket that connects while the bridge is reconnecting is closed with 4503 (retry, keep the saved login), not 4401. The page treats 4401 as "sign in again" and wipes its credential.
Tested on real hardware: lv1-mixer (Chrome headless, fake mic, over CDP through an ssh tunnel) as the remote phone, through the real tunnel and Cloudflare, with a throwaway enrolled Relay. Remote to local: talk heard 238 ms after press. Local to remote: about 0.9 s. Killing cloudflared mid-call: local audio had no gap, the remote page reconnected by itself in about 10 s.

## Tried and ruled out
TURN offered but not required: the bridge sometimes nominates the direct pair and then hangs in DTLS. Intermittent, which hid the cause.

## Watch out
After a reconnect the phone does not reopen audio on its own; the user presses Connect audio. The spec says it should resume. A 228 ms gap in local audio showed once, as the remote link re-formed.
Under 40 local phones the bridge reported a brief link loss (reconnecting for about 2 s) in one run; cause not found.
A minted enrollment code (relay-cloud: npm run codes -- mint) is shown once. The first user made through the API on a fresh install becomes the only admin path, so create an owner first. A throwaway Relay needs RELAY_CLOUDFLARED, its own state folder and ports 8080/8443.
In the code: app/modules/intercom_remote/media.py (_relay_when_turn, _Link.ensure), app/modules/intercom_remote/remote.py (unavailable), app/modules/intercom/presence.py (_hello), tests/test_intercom_remote.py.
