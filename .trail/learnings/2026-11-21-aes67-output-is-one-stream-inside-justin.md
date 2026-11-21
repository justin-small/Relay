---
title: "AES67 output is one stream inside the AES67 module, paced by Relay's own clock, and unproven on real Dante gear"
author: Justin
date: 2026-11-21
branch: main
cost: { minutes: 6, tokens: 158775 }
id: l-5dfb07a0
scope: repo
status: active
src: agent
touches:
  - app/modules/aes67/sender.py
  - app/modules/aes67/output.py
  - tests/test_aes67.py
---

## Decided
Issue 267: the AES67 sender lives in the existing aes67 module, not a new one. One multicast stream of up to 8 channels, 1 ms packets (48 frames), L24 or L16, announced by SAP every 30 s and withdrawn on stop. It is registered as core audio output `aes67_out` and as a lane-bus sink. A stage alert (`alert.sent`) plays a level-based tone on a chosen channel; `speak(language, pcm, rate)` is offered as `aes67.speak` and queues speech on that language's channel.

## Tried and ruled out
Locking to PTP: not done; the SDP says `ts-refclk:local` and Relay paces by its own clock. A configurable packet time: left out, 1 ms with 8 channels is the one size that fits a packet.

## Watch out
Never tried against a real Dante receiver: whether it accepts `ts-refclk:local` is unknown. Spoken channels have no audio source until #256 (text-to-speech) exists. Saving output settings must use `replace=("aes67",)` and include `remembered`, because config merges dicts and a removed language would otherwise stay. Alert tones go only to AES67, not to a sound device.

In the code: app/modules/aes67/sender.py, output.py, routes.py, tests/test_aes67.py.
