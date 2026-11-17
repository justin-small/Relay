---
title: "32 audio lanes patched from any device, crosspoint matrix, 48 kHz; rtmixer is optional"
author: Justin
date: 2026-11-17
branch: audio-lanes-92
cost: { minutes: 26, tokens: 182301 }
id: l-0c3d16e3
scope: repo
status: active
replaces: l-29b0ef38
src: agent
touches:
  - app/audiobus.py
  - tests/test_audiobus.py
  - requirements.txt
  - app/audio.py
---

## Decided
Issue 92, Justin's answers (2026-11-16): core gets a 32-lane audio bus instead of a second capture. Any device of any registered source patches to any lane; many consumers subscribe to a lane; a crosspoint matrix with gains routes lanes to outputs of up to 32 channels, and input can pass straight through as well as Relay's own alert and spoken audio. Lanes run at 48 kHz and each consumer resamples its own tap (captions stay 24 kHz). Outputs follow each lane's clock with a resampler trimmed by at most 0.5%. Talkback itself is left for a later change.
rtmixer (MIT) is used from a master commit, not its 2024 release, because master fixes a hang and teardown crashes. It is optional: without it core falls back to a plain sounddevice callback.

## Tried and ruled out
- rtmixer in requirements.txt: the desktop release installs hash-pinned packages and pip cannot hash a git URL, so it is in requirements-rtmixer.txt.
- Rust crates (rubato, rtrb): they would mean a compiled Python extension; only worth it if profiling on a low-end PC shows trouble.
- JACK, libsoundio, miniaudio: a server to run, a second backend, or a rewrite of the PortAudio path.

## Watch out
Proved on this Mac's mic and speakers only (1 and 2 channels); never with a 32-channel interface, and the fallback path had 3 underruns in 2.5 s there. The live-capture flag is now a count.

In the code: app/audiobus.py (core file), app/audio.py (LaneInput, LaneSink), /api/admin/audiobus, tests/test_audiobus.py.
