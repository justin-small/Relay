---
title: "Avantis live meters: subscribe over AH-Net 51321 and frame by length; groups 0 and 1 are the 96 pre-fader inputs"
author: Justin
date: 2026-11-22
branch: main
cost: { minutes: 1, tokens: 12293 }
id: l-8570ace1
scope: repo
status: active
replaces: l-564a1dda
src: agent
touches:
  - docs/avantis-meters.md
---

## Decided
Justin, 2026-11-22: MIDI (51325) has no meters; AH-Net (51321) does. Hello, a "Metering Sources" manager frame, then 11 subscribe frames makes the desk stream groups 0-2 and 4-8 at 50 Hz. Groups 0 and 1 are the 96 inputs (block n is input n+1) and are pre-fader; group 2 holds the mix buses (inferred). Full findings are in docs/avantis-meters.md.

## Tried and ruled out
Splitting meter frames with ahnet_frames (ends at the first F7): payload bytes can equal F7, so frames were cut and cells flashed to "no data". Frame by the length field instead.
Director's static-analysis doc says 51325 and a gain-word table: wrong port and scale for this desk; only its frame layout holds.
Probing with Director while it was connected to the live desk: Justin stopped it. Do not drive Director on the live mixer.

## Watch out
dB zero point and the FX/UFX/post-fader blocks are not confirmed. Group 2 labels are inferred from the mix order.
In the code: docs/avantis-meters.md, docs/avantis-meters/ (prototype viewer), ah.py (inputs capped at 64 by Model).
