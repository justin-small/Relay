
## Phase 3: benchmark

`bench/bench.py` runs the Rust bus and Relay's Python bus (with and without rtmixer) through the same scenarios on this Mac; see
`bench/RESULTS.md` for the numbers, how each was made fair and what it found (including an rtmixer ring-buffer bug in the Python bus).

**The Python arms no longer run from this tree.** `app/audiobus.py` now runs on the Rust core (#601), and the PortAudio lane
classes and the rtmixer path are removed, so `--engine python` and `python-nortmixer` have nothing to measure. The results in
`results/` are kept as the record. To rerun the Python bus, check out the commit before the wiring PR (`3c22095`, with
`requirements-rtmixer.txt`) and run `bench/bench.py` from there.
