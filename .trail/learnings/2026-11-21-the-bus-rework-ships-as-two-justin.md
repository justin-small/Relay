---
title: "The bus rework ships as two stacked PRs on top of the AES67 output PR, and server speech is left for later"
author: Justin
date: 2026-11-21
branch: aes67-output-267
cost: { minutes: 1, tokens: 1582 }
id: l-61ca5823
scope: repo
status: active
src: agent
touches:
  - app/audiobus.py
  - app/audio.py
  - app/modules/aes67/output.py
---

## Decided
Justin, 2026-11-20, for issue 599 (all audio through the bus):
- Start from the AES67 output branch (PR 600), so the AES67 sender can become a bus output in the same work; the PR retargets main once 600 merges.
- Two stacked PRs. First: the bus is always on, a fresh install gets a default lane and output, capture reads a lane, and old input settings are migrated. Second: named destinations, the patch UI, AES67 as an output, and the test that enforces it.
- "Spoken translation: <language>" gets a destination and a play-to-destination call only. Server-side speech generation is a separate issue.
- Justin wants existing audio engines, network audio stacks and routing designs researched and talked over before any architecture choice for the bus.

## Tried and ruled out
- Merging PR 600 first, or building on main and moving AES67 later: not chosen.
- One PR for the whole issue: too big to review.
- Building server-side speech generation in 599: out of scope.

## Watch out
- Alert tones must still sound within 500 ms even though the bus delays passthrough by latency_ms.
- The AES67 module's speak hook is provided but nothing on the server calls it.

In the code: app/audiobus.py (Bus, _Output.inject, BusOutput), app/audio.py (get_output, register_output), app/modules/aes67/output.py (speak, on_alert), config keys audio_bus, audio_source, audio_device, input_channel.
