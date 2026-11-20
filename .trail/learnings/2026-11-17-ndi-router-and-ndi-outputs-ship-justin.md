---
title: "NDI router and NDI outputs ship in one PR: routing through the NDI runtime directly, outputs limited to Screens and Overlays"
author: Justin
date: 2026-11-17
branch: ndi-viewer-signage-widgets
cost: { minutes: 7, tokens: 52373 }
id: l-a8facc01
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/runtime.py
---

## Decided
Justin, 2026-11-17, issues 298 and 299: build #299 first, then #298, in one PR. #299 covers the renderer spike, the output registry and the Screens and Overlays outputs only; signage, multiview, boards and audio come later. #298 covers routed outputs, the crosspoint page, presets (salvos) and persistence. The Companion HTTP API, the routing permission and the ProPresenter [relay:ndi=...] tag are left out of this PR. Routing calls NDIlib_routing_* on the NDI runtime through ctypes. Justin also wants a BirdDog source read and a Magewell driver; the Magewell driver is written from Magewell's docs and stays untested until he enters a decoder's address and login in Relay's settings page.

## Tried and ruled out
- Receiving and re-sending frames through cyndilib to fake routing: re-encodes video, against the issue's intent.
- Patching or forking cyndilib for routing bindings: slowest option.
- Doing all of #299 (every output, audio) in this PR: too large.

## Watch out
- cyndilib does not wrap the routing API, but the libndi it bundles exports it. Proved 2026-11-17 on macOS: create a router source, change it to a sender, and a receiver on the router got the video.
- cyndilib's Sender.open() fails with "No frame objects" unless a VideoSendFrame is attached first; the current _Sender in runtime.py does not attach one.

In the code: app/modules/ndi/runtime.py (_Sender, where routing bindings go).
