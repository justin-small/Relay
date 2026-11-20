---
title: "Remote phones on mobile networks connect through Cloudflare TURN, which Relay's cloud issues; the zone allows 100 cloud calls per 10 s per IP"
author: Justin
date: 2026-11-15
branch: intercom-remote-277-media
cost: { minutes: 20, tokens: 157451 }
id: l-f3fed1ef
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/cloud.py
  - app/modules/intercom_remote/media.py
---

## Decided
Issue 277, 2026-11-15. A T-Mobile iPhone sent hundreds of ICE checks to Cloudflare's SFU (one IPv4 UDP address, 141.101.90.0:1473) and got no answers, while Wi-Fi worked. relay-cloud now has POST /v1/realtime/turn: it generates 4-hour credentials from a TURN key held only in the Worker (secrets CF_TURN_KEY_ID, CF_TURN_KEY_TOKEN), dropping port 53 and non-Cloudflare URLs. Relay reuses them for its phones for an hour and gives them with cf.session; without them phones still get STUN. Verified: iPhone on cellular and a Mac crew page heard each other.
The Cloudflare account needed a Realtime subscription (free 1,000 GB/month) before a TURN key could be made; the dashboard warned SFU access could be interrupted without one.
The zone rate-limit rule on cloud./v1/* was 10 requests per 10 s per IP. One phone connecting makes about 10 calls in 2-3 s, all from the church's one IP, so it locked itself out. Raised to 100 per 10 s.
With TURN the phone's SDP answer passes 4 KB, the intercom socket's message limit; a remote connection now allows MAX_SDP + 1 KB (Remote.max_message).

## Tried and ruled out
- Pushing all of a phone's channel mixes in one Cloudflare call (fewer calls): the bridge's outgoing aiortc connection never connected when its first offer carried two tracks, with or without max-bundle. Reverted to one push per channel.

## Watch out
relay-cloud's TURN work is on branch turn-credentials in ~/Downloads/relay-cloud-gh, deployed as version ada7d353 (previous live version ec0398e2). ~/Downloads/relay-cloud is an old copy without a remote.

In the code:
- relay-cloud src/realtime.ts: turn(), iceUrl()
- app/modules/intercom_remote/cloud.py: ice_servers(); remote.py: RemoteCrew.max_message; intercom/presence.py: Remote.max_message
