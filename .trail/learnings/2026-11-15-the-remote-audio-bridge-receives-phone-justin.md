---
title: "The remote audio bridge receives phone microphones on a second Cloudflare connection, because its own offers renumber Opus and every mic packet was dropped"
author: Justin
date: 2026-11-15
branch: intercom-remote-277-media
cost: { minutes: 22, tokens: 163404 }
id: l-d0cd2529
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/static/phone.js
  - app/modules/intercom_remote/remote.py
  - app/modules/intercom_remote/media.py
---

## Decided
Issue 277, 2026-11-15. With one Cloudflare connection for both the pushed channel mixes and the pulled phone microphones, a remote phone's talk never reached the rooms: aiortc's own offers (adding a mix) renumber its Opus payload type to 96 on every m-line, Cloudflare keeps forwarding the phone's mic as the browser's 111, and aiortc's router drops packets whose payload type is not registered for that receiver. The bridge now has two links: `out` (mixes, the bridge always offers) and `into` (mics, Cloudflare always offers, so aiortc keeps its payload types). The talk participant also waits to be armed before publishing; publishing earlier hung about 10 s until the SDK gave up. The phone page plays each channel on its own audio element; one element played only the last channel pulled.

## Tried and ruled out
- Offering plain Opus only from the phone (dropping RED): not the cause, the phone then offered only 111 and packets were still dropped.

## Watch out
Headless Chrome reports totalAudioEnergy 0 for received audio because it has no speaker; measure with a WebAudio analyser. A local phone page does not rejoin LiveKit after Relay restarts until refreshed. Verified 2026-11-15: iPhone on Wi-Fi and a Mac crew page heard each other. Cellular (T-Mobile) still needs TURN.

In the code:
- app/modules/intercom_remote/media.py: `_Link`, `LiveMedia.out` / `.into`, `_can_publish`
- app/modules/intercom_remote/static/phone.js: `pc.ontrack`, players
