---
title: "The bridge opens its Cloudflare session only when a phone first needs it, because an unused session is refused after a few seconds"
author: Justin
date: 2026-11-15
branch: intercom-remote-277-media
cost: { minutes: 29, tokens: 141210 }
id: l-7d646b7d
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/cloud.py
  - app/modules/intercom_remote/remote.py
  - app/modules/intercom_remote/media.py
  - app/modules/intercom_remote/static/phone.js
  - tests/test_intercom_remote.py
  - app/modules/intercom_remote/static/phone.html
  - app/modules/intercom_remote/routes.py
---

## Decided
The audio bridge now makes its own Cloudflare session and connection when the first channel mix or phone microphone needs one, and closes both when the last one goes. Cloudflare Realtime refuses calls on a session that nothing has connected to within a few seconds, so a session opened when Relay starts and used minutes later failed with a slow 502 that showed as "Cloudflare Realtime refused that just now".

## Tried and ruled out
- Keeping the session the bridge opens at start (bridge.session_id): fine for the first seconds after a restart, which hid the problem in every quick test.
- Retrying the microphone pull 15 times: each try is a call to the cloud, which answers 429 after about 30 a minute per install, and a 429 with an unknown error name showed as "doesn't understand".

## Watch out
Relay's cloud limits an install to about 30 calls a minute. Test clients that hammer it can lock a phone out for a minute. Restarting the test Relay by killing livekit-server separately sometimes left LiveKit in an error state; check port 7880 answers before testing.

In the code:
- app/modules/intercom_remote/media.py: `_ensure`, `_reset`, `_idle`
- app/modules/intercom_remote/cloud.py: 429 becomes rate_limited
