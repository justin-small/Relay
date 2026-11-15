---
title: "Phones sign in with a per-device credential over /intercom/ws, and Relay mints one-room LiveKit tokens that cannot publish"
author: Justin
date: 2026-11-15
branch: intercom-phones-rooms
cost: { minutes: 1, tokens: 3047 }
id: l-4b32005c
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/devices.py
  - app/users.py
---

## Decided
Justin chose (2026-11-15, issues 239 and 273): build #273 first, then #239 on it, and measure server-side latency only (phone CPU and battery stay with him). The credential is 32 random bytes shown once, stored as SHA-256, rotated after 30 days, with the old hash valid 10 minutes; a phone unused 30 days expires. LiveKit tokens are signed by hand (no livekit-api package): one room, 5 minutes, canPublish false, microphone only. Publishing is switched on and off in the running room with UpdateParticipant; a removed member is swept out of the room by a hook on store changes. Core got one hook list, users.signed_out_hooks, so a password change, disable, delete or sign-out-everywhere reaches the module without core importing it.

## Tried and ruled out
Putting the room name only in the request body: LiveKit's room service answers 401 unless the admin token also names the room.

## Watch out
- Measured against real livekit-server 1.13.11 with its Python rtc client: a phone without a lease never gets a publish accepted, grant and revoke reach it in milliseconds, and a removed member is disconnected at once. Rooms per phone, battery and real phone browsers were not measured.
- A phone reaches LiveKit only through the /intercom/rtc proxy (#238); the welcome message names it as rtc_path.

In the code: app/modules/intercom/devices.py, presence.py, rooms.py, routes.py; app/users.py signed_out_hooks
