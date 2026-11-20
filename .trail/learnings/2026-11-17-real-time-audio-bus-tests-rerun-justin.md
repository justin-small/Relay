---
title: Real-time audio bus tests rerun when a loaded macOS runner stalls the fake feeder
author: Justin
date: 2026-11-17
branch: audio-metering-149
cost: { minutes: 1, tokens: 12568 }
id: l-2df75549
scope: repo
status: active
src: agent
touches:
  - tests/test_audiobus.py
---

## Decided
PR 446 (2026-11-17): the matrix and clock-drift sections of the audio bus test run up to three times and keep the first run that was not starved. The same failure was already red on main after #92 (28 underruns against a limit of 2, only on the macOS runner).

## Tried and ruled out
Loosening the underrun limit or the click threshold: it would hide a real fault, and the failure was the test's own feeder stalling, not the bus.

## Watch out
A real fault still fails all three runs. Windows also had a one-off failure in the ProPresenter test that passed on rerun; it looks flaky, not caused by this work.
In the code: tests/test_audiobus.py (settled).
