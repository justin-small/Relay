---
title: "The remote audio bridge imports livekit and aiortc lazily, mixes each channel once, and its SDK calls are unverified without real sessions"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 3, tokens: 88114 }
id: l-0a733e55
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/media.py
  - tests/test_intercom_remote.py
---

## Decided
media.py (issue 277) is the real audio bridge. One bridge participant per channel mixes all room audio into a single 48 kHz mono track pushed to Cloudflare; a remote talker's mic is pulled into the bridge's Cloudflare session and published into the room under the phone's own u<user>-d<device> identity so the arbiter arms and mutes it. livekit and aiortc are not added to requirements (release deps are hash-pinned for Python 3.15); a missing one shows as the bridge's "missing" state.

## Tried and ruled out
- Mix-minus per phone: one mix per channel is simpler, so a remote talker hears a little of their own voice.

## Watch out
Only the mixer, down-mixing and fallback are tested. The livekit and aiortc calls were checked against installed signatures in a scratch venv but never run against LiveKit or Cloudflare; the issue's real-audio checkboxes stay unchecked. Publishing is retried for about 3 s because LiveKit refuses it until the arbiter arms the participant from its webhook.

In the code:
- app/modules/intercom_remote/media.py: `LiveMedia`, `Mixer`
- tests/test_intercom_remote.py: "the audio helpers"
