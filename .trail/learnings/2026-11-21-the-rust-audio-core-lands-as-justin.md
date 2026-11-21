---
title: "The Rust audio core lands as a spike-only first PR with no Python fallback, and the output-meter offset is a bench averaging artifact"
author: Justin
date: 2026-11-21
branch: rust-bus-spike-599
cost: { minutes: 3, tokens: 21751 }
id: l-2a81f71b
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-21, issue 601: PR 1 is the spike branch only (the Rust workspace, CI and cargo-deny), with nothing in app/ using it yet. The Python bus is removed outright when the wiring lands, with no fallback, so the Windows and Linux benchmarks must pass before that wiring PR merges. The wheel is required for everyone (run_all.py builds it, every contributor needs cargo). All bench results and tools/bench_fastrapi are committed. Windows is real hardware; Linux is CI only unless a box turns up. Mic permission on a notarized build is not blocking while releases are not notarized.
The 1.6-2.6 dB low output-meter reading is not an engine bug: the bench averaged linear block RMS, and the 31-sine test signal (all in phase, 33 ms crest period) fluctuates -17 to +5 dB in 10 ms blocks, so that average reads about 2.9 dB low. Averaging power (sqrt of mean rms squared) gives the expected -58.55 dBFS.

## Tried and ruled out
Keeping the Python bus as a fallback for a release or two: dropped by Justin; the audiobus tests are the safety net instead.
Dropping the raw bench JSON from the commit: Justin chose to keep everything.
A bug in meter.rs: its RMS and peak code is plain and correct.

## Watch out
The engine's own meter was not yet run on this signal; a Rust test should check the power average lands within 0.1 dB of expected before the offset question is closed in RESULTS.md.
Because the wheel is required and there is no fallback, a broken wheel build blocks all work.

In the code:
native/relay-bus/bench/bench.py:840 (output_error_db averaging), native/relay-bus/crates/relay-bus/src/meter.rs, native/relay-bus/bench/RESULTS.md (Meter sanity).
