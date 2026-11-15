---
title: "Intercom spike: a new track takes 400-700 ms to reach listeners, a server unmute 70 ms, and a muted published track can be unmuted by the phone"
author: Justin
date: 2026-11-15
branch: intercom-phones-rooms
cost: { minutes: 8, tokens: 56421 }
id: l-4317a1a2
scope: repo
status: active
src: agent
---

## Decided
Justin asked for the #239 spike at 10 rooms with 20 people each and people sharing rooms: 110 people (50 in one room, 30 in two, 30 in three), 200 memberships, 210 simultaneous room connections, one talker per room speaking at once.

## Tried and ruled out
Muted published track as the talk path (the #239 fallback): the server can unmute it in about 70 ms, but it needs enable_remote_unmute on in livekit.yaml, and a phone that already holds canPublish can unmute its own track after the server muted it, so it fails "a phone without a lease cannot publish". Publishing only after the lease is granted keeps that rule but costs 400-700 ms before listeners hear anything, over the 100 ms target.

## Watch out
- Clients were the Python rtc SDK, 8 processes on the same Mac as livekit-server (about 400% CPU for the clients), not Safari or Chrome. Per-phone CPU and battery in a real browser are unmeasured.
- livekit-server with 210 connections and 10 talkers: about 60% of one core and 500 MB; idle with everyone connected, about 8% and 390 MB.
- A phone listening in N rooms with a talker audible in each costs roughly 2% of a core per room in the SDK client (1 room 3.6%, 3 rooms 6.2%, 6 rooms 12.9%).
- Lease grant to the talker's phone seeing the permission: 30 ms. Removing a member from a room disconnects it in about 10 ms, and their other rooms stay up.
- LiveKit's room service answers 401 to an admin token that does not name the room.
