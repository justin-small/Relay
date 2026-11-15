---
title: "A phone with Talk selected is armed: it publishes muted and a talk lease only unmutes it, and Relay polls to re-mute self-unmuted tracks"
author: Justin
date: 2026-11-15
branch: intercom-phones-rooms
cost: { minutes: 9, tokens: 49610 }
id: l-eecfd1f2
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/rooms.py
---

## Decided
Justin chose (2026-11-15, #239 spike follow-up) to pre-publish when armed, so a lease starts audio in about 85 ms instead of 400-700 ms. A phone is armed when it is a Talk member and has Talk selected on the channel; it gets publish rights, publishes at once, and Relay keeps the track muted. A lease unmutes it and its end mutes it. A phone that is not armed cannot publish at all. LiveKit has no event for a phone unmuting itself, so Relay polls armed rooms every 0.3 s and mutes any open track without a lease; a phone that does this is heard for roughly 100-340 ms. A full reconcile every 10 s heals drift after a restart.

## Tried and ruled out
Waiting for a webhook on unmute: LiveKit sends none (only publish and unpublish). Server-side mute alone as the guard: a phone with publish rights can unmute itself, which is why the polling exists.

## Watch out
- This gives up "a phone without a lease cannot publish" for phones that have Talk selected; Justin accepted that.
- livekit.yaml now sets enable_remote_unmute, which the server needs to unmute a talker's track.
- Measured at 10 rooms of 20 with overlapping members: lease start median 84 ms, p95 110 ms; lease end median 97 ms. Shorter polling shrinks the leak and costs Relay CPU (7% of a core at 0.3 s with 10 armed rooms).

In the code: app/modules/intercom/rooms.py (armed, talking, apply, set_talking, enforce, watch)
