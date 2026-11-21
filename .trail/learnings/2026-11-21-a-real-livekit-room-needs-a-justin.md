---
title: "A real LiveKit room needs a 120 ms mixer buffer, and tone tests must allow the engine's rate trim"
author: Justin
date: 2026-11-21
branch: rust-intercom-mixer-605
cost: { minutes: 11, tokens: 73227 }
id: l-a7d61ccc
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/media.py
  - app/modules/intercom_remote/mixbus.py
  - tests/test_intercom_remote_media.py
  - native/relay-bus/README.md
  - native/relay-bus/crates/relay-bus-py/src/lib.rs
  - native/relay-bus/crates/relay-bus/src/lib.rs
  - native/relay-bus/crates/relay-bus/src/link.rs
  - native/relay-bus/crates/relay-bus/tests/link.rs
  - tests/test_intercom_remote.py
---

## Decided
Justin, 2026-11-21, issue 605: the intercom mixer is tested against a real livekit-server and the real LiveKit SDK (tests/test_intercom_remote_media.py), not only the engine. The mixer outputs are held 120 ms deep (60 ms of audio), not 40: at 40 ms a real room's jittery frames made the output underrun constantly (13 to 187 underruns in seconds, 0 at 120 ms).

## Tried and ruled out
Judging the mix by the level at each tone's exact frequency: the engine trims a pull output's rate by up to 0.5 % to follow its sources, so a 2500 Hz tone sits 12 Hz off and looked like a dropout. Measure the strongest bin within 2 % instead.

## Watch out
A busy Python event loop is not fully isolated: aiortc's recv pulls the mix on that loop, so while it is blocked nothing is pulled and the 500 ms catch-up burst afterwards is silent (about 24 of 25 frames). What the engine does keep: the main-bus feed through the Rust link, and a recovery of about 1 s. Fully hiding a stall needs the pulling to leave the loop.
The test downloads the pinned livekit-server on Linux and Windows and skips on a Mac without the binary on PATH.

In the code:
app/modules/intercom_remote/mixbus.py (LATENCY_MS), media.py (_hear, _join), tests/test_intercom_remote_media.py.
