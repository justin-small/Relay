---
title: "The Rust audio core keeps sound flowing when Python is busy, but costs as much CPU as the Python bus under heavy load"
author: Justin
date: 2026-11-21
branch: rust-bus-spike-599
cost: { minutes: 25, tokens: 45821 }
id: l-e392bf27
scope: repo
status: active
src: agent
touches:
  - app/audio.py
---

## Decided
Measured 2026-11-21 on an M4 Pro Mac, 60 s runs, both buses at 60 ms latency. With 32 lanes and a full matrix, Rust used 55% of a core, the Python bus 53% with rtmixer and 59% without. With a busy Python thread, the Python bus lost almost all its audio while Rust running on its own lost none. Alerts were under 50 ms everywhere. Justin cut the event-loop and busy-Python scenarios to one run per engine to save time, so those two have no spread.

## Tried and ruled out
Using the same high-quality sinc resampler on every lane of every output in Rust: it is what makes Rust as costly as Python under load, because the Python bus uses linear interpolation. Bypass it when the ratio is near 1, or use a cheaper interpolator.

## Watch out
- Rust memory grew about 149 MB in every heavy 60 s run; not yet known whether it is a leak.
- With rtmixer 0.1.7 the Python bus's speaker ring action can end for good while the stream still reports active, so the supervisor never reopens it. The repo's .venv had 0.1.7 and soxr 0.5.0, not the pinned versions, so check on the pinned build first.
- The benchmark feeds Rust's test inputs from Python threads, so Rust's busy-Python numbers include the GIL wait of the harness itself.

In the code: native/relay-bus/bench/bench.py, native/relay-bus/bench/RESULTS.md, native/relay-bus/crates/relay-bus/src/resamp.rs, app/audio.py (play_ringbuffer sink).
