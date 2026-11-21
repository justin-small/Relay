---
title: "NDI audio, sync, watch, SCTE-104 and Bridge are built in Rust, with what each can and cannot tell"
author: Justin
date: 2026-11-21
branch: ndi-audio-sync-alerts-bridge
cost: { minutes: 30, tokens: 513021 }
id: l-1f17a860
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/__init__.py
  - app/modules/ndi/analysis.py
  - app/modules/ndi/bridge.py
  - app/modules/ndi/routes.py
  - app/modules/ndi/service.py
  - app/modules/ndi/static/ndi-analysis.js
  - app/modules/ndi/static/ndi-bridge.js
  - app/modules/ndi/static/ndi.js
  - app/modules/ndi/templates/ndi.html
  - docs/ndi-analysis.md
---

## Decided
Justin, 2026-11-21: finish the open NDI issues (#301, #303, #560, #561, #562) in one PR, as much in Rust as possible, tested on this Mac only.
- A/V sync is the audio's timestamp-to-timecode gap minus the picture's (median of 120 frames). It cannot see a delay the sender's timecodes do not carry, reads nothing for SDK-made timecodes, and a sender that calls audio and picture in turn adds up to one frame by call order alone; read changes, not the absolute value.
- NDI sound joins the 32-lane bus as an input and an output (the bus-only rule), not a private sound path. Browser listen is PCM over HTTP, no sound device.
- Audio routed separately from a picture is a Rust combiner (decode and resend), because NDI routing switches a whole source.
- SCTE-104 is read from `<vancData>` XML in video-frame metadata (NDI's proposed metadata messages).
- NDI Bridge: Relay lists, checks and writes start-up text only; no key stored.
## Tried and ruled out
Sound from a page output (signage video): the headless browser captures pictures, not sound. A moving meter on the sources list: floods live updates.
## Watch out
Proved on real NDI loopback on this Mac only (sync offsets, audio-only source and combiner, bus lanes both ways, SCTE example). Not proved: a second machine, HX or hardware senders, a real broadcast SCTE sender, a real Bridge.
In the code: native/relay-ndi/crates/relay-ndi/src/{health,sink,watch,scte,pcm}.rs, app/modules/ndi/{bus,listen,watch,bridge}.py, docs/ndi-audio.md, docs/ndi-bridge.md.
