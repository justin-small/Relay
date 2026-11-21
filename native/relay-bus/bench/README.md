
## Phase 3: benchmark

`bench/bench.py` runs the Rust bus and Relay's Python bus (with and without rtmixer) through the same scenarios on this Mac; see
`bench/RESULTS.md` for the numbers, how each was made fair and what it found (including an rtmixer ring-buffer bug in the Python bus).
