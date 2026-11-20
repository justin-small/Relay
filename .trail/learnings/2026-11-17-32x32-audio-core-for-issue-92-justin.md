---
title: "32x32 audio core for issue 92: lanes patched from any device, crosspoint matrix, 48 kHz, talkback left out"
author: Justin
date: 2026-11-17
branch: site-connecting-piece
cost: { minutes: 11, tokens: 78243 }
id: l-29b0ef38
scope: repo
status: active
src: agent
touches:
  - tests/test_modules.py
  - app/audio.py
  - app/engine.py
---

## Decided
Issue 92 (2026-11-16, Justin's answers to my questions): instead of a second AudioCapture, core gets a 32-lane audio bus. Inputs are 32 mono lanes; the operator patches any (device, channel) from any source, including several devices, to any lane. Consumers (engine, later talkback, routes, modules) subscribe to a lane and many can subscribe to one lane. Routing is a crosspoint matrix, 32 inputs to N destinations, with live passthrough input to output as well as Relay's own alert and spoken audio. Outputs take up to 32 lanes to a device or a module's sender. Lanes run at 48 kHz; each consumer resamples its own tap (the captions path stays 24 kHz mono). Outputs drift-correct with an adaptive buffer, since devices run on different clocks.
First delivery is the lanes, matrix and outputs only. The talkback integration (#92's own settings, refuse-same-lane check) is left for a later PR.

## Tried and ruled out
- Fixed contiguous blocks per device: less flexible than patching.
- Native rate per lane, and 24 kHz lanes: the first makes a mixed-rate matrix complicated, the second makes passthrough poor quality.
- Best-effort clicks or same-clock-only routing: not accepted for multi-device passthrough.
- Third-party libraries (MIT candidates: python-rtmixer with pa-ringbuffer, JACK-Client, libsoundio, miniaudio) were being compared when this was written; no choice yet, my lean was PortAudio plus our own numpy matrix.

## Watch out
The single live-capture flag in the audio module must become a count, or the device list gets re-probed under a running stream. A new core file for the bus would need adding to CORE_FILES in tests/test_modules.py, with a reason in the PR.

In the code: app/audio.py (AudioCapture, AudioSource, AudioOutput, capture_is_live), app/engine.py, config keys audio_device, audio_source, input_channel.
