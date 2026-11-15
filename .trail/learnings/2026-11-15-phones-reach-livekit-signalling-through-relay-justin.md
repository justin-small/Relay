---
title: "Phones reach LiveKit signalling through Relay's HTTPS port, admitted by their join token and a live Relay socket"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 1, tokens: 1217 }
id: l-0bedd08f
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/rtcproxy.py
  - tests/test_intercom_rtc.py
---

## Decided
The /intercom/rtc proxy (issue 238) accepts a phone only if its LiveKit join token verifies (HS256, our key, expiry required, roomJoin for one ch-<id> room) and that phone is connected on /intercom/ws right now. Bad attempts count against the same per-address limit as bad hellos. Signing out, evicting a user or stopping the module closes the phone's proxy sockets. Refused before accept, so the handshake gets a 403.

## Tried and ruled out
Passing a proxy option to websockets.connect: the pinned websockets 13.1 has none (added later).

## Watch out
websockets 13.1 uses the legacy API: a server handler takes (ws, path), not ws.request.

In the code: app/modules/intercom/rtcproxy.py, presence.py (revoked, drop_user), tests/test_intercom_rtc.py
