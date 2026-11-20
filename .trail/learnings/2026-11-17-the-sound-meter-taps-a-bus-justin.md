---
title: "The sound meter taps a bus lane, uses numpy only, and boards get its readings through an opt-in status feed"
author: Justin
date: 2026-11-17
branch: audio-lanes-92
cost: { minutes: 10, tokens: 159589 }
id: l-e2a94c05
scope: repo
status: active
src: agent
---

## Decided
Issue 149, Justin's answers (2026-11-17): build core only (native-rate tap, A/C SPL with calibration, 1/3-octave RTA, status values, admin card) plus board widgets; no history, stream-mix report or modules this pass. The tap is a subscriber on one lane of the #92 bus, so a measurement mic is just another patched input. Boards read it from /stream?stream=status&sound=1; the default status feed (panels) is unchanged.

## Tried and ruled out
- Rectangular-window FFT for the weighted level: reads 100 Hz about 1.8 dB off (leakage into bins the weighting treats differently). A Hann 4096 window is within tolerance.
- scipy: not a dependency; the A/C curves are applied per FFT bin with numpy.
- Putting `sound` in the default status feed: it changes every second and would resend the whole state to panels each poll.

## Watch out
Never tested on a real measurement mic or against a reference meter, only synthetic tones. In the code: app/metering.py, tests/test_metering.py. Not built: spectrogram on the admin page, per-service history, #149's further scope.
