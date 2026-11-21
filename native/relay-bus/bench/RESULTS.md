# Rust bus against Python bus: benchmark results (issue 599, phase 3)

Raw runs: `results/raw/*.json` (one file per run), aggregated in `results/summary.json`. Reproduce with
`.venv/bin/python native/relay-bus/bench/bench.py --all` (or `--engine python|python-nortmixer|rust --scenario A..G`).
Spread is `median (min-max)` over the repeats. **A, B and C are three 60 s runs each. D and E are ONE run per engine (a change of
plan cut them short), so they have no spread and one run says little about the tails. F is one Rust run. G was written but
not run (see Caveats).**

## Machine

| | |
|---|---|
| Model | Mac16,7 (MacBook Pro), Apple M4 Pro, 14 cores (10 performance, 4 efficiency), 24 GB |
| OS | macOS 26.5.2 (build 25F84) |
| Python | 3.10.11 (the repo `.venv`), numpy 2.2.6 (Accelerate), soxr 0.5.0, sounddevice 0.5.6, rtmixer 0.1.7 |
| Rust wheel | `relay_bus` 0.1.0, release build, cpal backend |
| Power | AC power, battery 100%; run on 2026-11-20/21; load average about 3.5 at the start (other apps open, including this session) |
| Real devices | MacBook Pro Microphone (default input) and MacBook Pro Speakers (default output) |

## Engines

* `rust`: `relay_bus.Bus("cpal")`.
* `python`: `app/audiobus.py` `Bus()` with rtmixer (C callbacks and ring buffers), which is what Relay runs when rtmixer is installed.
  rtmixer was already in the `.venv`; nothing was installed.
* `python-nortmixer`: the same bus with `audio.rtmixer = None` (set in the bench process), so PortAudio callbacks are Python and hold the GIL.

Both use `latency_ms = 60`, which is both engines' default. In each, that gives each lane's ring a target fill of 30 ms
(Python: `RATE * latency_ms / 2000`; Rust: half the latency, floor of three 256-frame blocks = 16 ms).

## Results

### A. Light (60 s x 3): mic -> lane 1 with a 24 kHz tap read continuously; 2 ch speaker fed by one synthetic lane at -60 dB

| Metric | rust | python | python-nortmixer |
|---|---|---|---|
| Process CPU, % of one core | **4.7** (4.7-4.7) | 11.5 (11.3-11.6) | 8.2 (8.2-8.5) |
| RSS at end, MB | **54** (38-55) | 104 (103-105) | 104 (102-104) |
| Speaker underruns | 0 | 0 | 6 (4-6) |
| Resyncs / input frames dropped | 0 / 0 | 0 / 0 | 0 / 0 |
| Tap samples vs expected (24 kHz) | 1.0003 | 1.0000 | 1.0000 |
| Tap dropped | 0 | 0 | 0 |
| Longest gap between tap reads that returned data, ms | **15** (15-19) | 43 (42-46) | 44 (41-45) |

### B. Heavy (60 s x 3): 32 lanes, speaker + three 8 ch fake outputs, full matrix at -40 dB, four 24 kHz taps

| Metric | rust | python | python-nortmixer |
|---|---|---|---|
| Process CPU, % of one core | 54.7 (54.2-54.9) | **52.5** (51.5-53.5) | 59.3 (58.5-59.6) |
| RSS at end, MB | 264 (234-265) | **143** (143-144) | 100 (98-143) |
| RSS growth over the 60 s window, MB | 149 (118-150) | **33** (32-33) | -11 (-13 to 33) |
| Speaker underruns (60 s) | 0 (0-**93**) | 19 (8-30) | 222 (207-288) |
| Fake outputs underruns, 3 outputs | 0 (0-62) | 93 (50-101) | 76 (39-119) |
| Resyncs; silent output blocks; input frames dropped | 0; 0; 0 | 0; 0 (0-3); 0 | 0; 0; 0 |
| Tap dropped / waveform discontinuities / samples vs expected | 0 / 0 / 0.9999 | 0 / 0 / 1.0000 | 0 / 0 / 1.0000 |
| Tap longest read gap, ms | 51 (37-59) | 50 (48-52) | 47 (46-48) |
| Input driver lateness p99 / max, ms (harness check) | 4.6 / 14 (11-42) | 5.3 / 8.8 | 4.8 / 6.4 |

CPU by thread group in B, % of one core (medians): Rust: output mixing in the Rust pull calls on the bench's drain threads **33.5**,
cpal/Rust threads 9.8, input pushes 6.2, tap readers 5.1. Python: the bus's own output threads **39.4**, input deliveries 5.1, sink
test doubles 5.0, tap readers 1.8, native 0.7. Python without rtmixer: bus threads 41.2, native (PortAudio callbacks) 4.5.

### C. Alert latency (B plus 20 tones, 1 kHz, -20 dBFS, 300 ms, at random moments; 3 runs, 60 tones pooled)

Requirement: under 500 ms. **All three engines pass by a wide margin (worst case 47 ms).**

| Measured from the `inject` call to... | rust | python | python-nortmixer |
|---|---|---|---|
| Speaker output meter rising (above -30 dBFS RMS): median / p95 / max, ms | 10.9 / 16.1 / 17.8 | 5.5 / 10.6 / 13.8 | 8.4 / 16.6 / 19.4 |
| Samples consumed by a not-real device (fake output f1, drained on the wall clock; exact): median / p95 / max | **5.8 / 7.6 / 8.7** | 27.0 / 32.9 / 33.6 | 27.4 / 32.4 / 35.2 |
| Real speaker, estimated hand-off (meter time + the ring the clip waits behind), median / p95 / max | not available (see below) | 33.0 / 37.8 / 38.9 | 37.2 / 45.7 / 46.9 |
| The `inject` call itself, median / max | 2.3 / 4.1 | 0.2 / 3.2 | 0.2 / 2.0 |
| Meter peak during a tone vs the -20.0 dBFS sent | +0.42 dB | +0.40 dB | +0.40 dB |

The real speaker's device callback cannot be observed from Python for either engine, so "handed to the device" is measured on the
fake output, which every engine drains on the same schedule (a 5 ms tick). That is the fair number. The Python bus mixes a clip into
its ring up to about 30 ms ahead of the device (that is its ring's target fill), so its tone reaches a device about 27 to 33 ms after
the call; Rust adds the clip to the block the device is filling, after its lane buffers, so it arrives in the next block. The meter
column is the reverse (Python reads sooner) because Python's meter measures the mix when it is made, ahead of the device, while Rust's
measures the device block. The +0.4 dB on the peak is the background (31 lanes, up to -40.6 dBFS) adding to the tone; both engines'
meters read the same, rounded to 0.1 dB.

### D. GIL and event loop (B plus an asyncio loop on a 5 ms timer): ONE run per engine

Lateness of a 5 ms `asyncio.sleep`, over about 10,000 ticks. A process with no bus at all measured p50 0.72 ms, p99 about 1-2 ms.

| | rust | python | python-nortmixer |
|---|---|---|---|
| p50, ms | 0.73 | 0.76 | 0.82 |
| p99, ms | 3.56 | 3.36 | 2.67 |
| max, ms | **50.2** | 6.0 | 4.2 |
| Process CPU % | 53.7 | 51.4 | 60.9 |

At this load neither engine disturbs a 5 ms timer in a way a web server would feel (p99 under 4 ms for all). Rust's single 50 ms
outlier is one event in one run; I did not find its cause (the process was also running the bench's own 3 drain threads and the
generator, all Python threads that re-take the GIL after each Rust call). It would need repeats to say whether it is a pattern.

### E. Python busy (B plus a tight CPU-bound pure-Python thread, the worst case): ONE run per engine

The busy thread kept 92% (python), 91% (python-nortmixer) and 97% (rust) of the loop speed it has alone in the process.

| | rust | python | python-nortmixer |
|---|---|---|---|
| Process CPU % | 111 | 102 | 102 |
| Synthetic input that still arrived (tap on a synthetic lane, samples vs expected) | 0.57 | **0.04** | 0.03 |
| Mic tap (real device, samples vs expected) | 1.00 | 1.00 | 0.71 |
| Speaker underruns (per-lane events) | 20,130 | 0 (see next row) | 2,884 |
| Speaker: rtmixer ring action ended (silence until re-armed) | n/a | **2,489 times** | n/a |
| Fake outputs underruns / silent output blocks | 55,623 / 0 | 16,402 / 16,253 of 16,506 | 16,380 / 16,231 of 16,465 |
| Resyncs (speaker + fake) | 0 + 211 | 155 + 587 | 155 + 570 |

How to read E, because the harness limits it. The synthetic inputs and the not-real outputs are fed and drained by Python threads
in both engines. A tight loop makes every thread that gives up the GIL wait up to 5 ms to take it back, and Rust's `push` and
`pull` release the GIL on every call. So Rust's input starvation here (0.57 of the audio arrived) is mostly the harness: in Relay
a real device's input never needs the GIL on the Rust side. For the Python bus there is no such separation: its mixing,
metering and resampling are Python on the same GIL, so starvation there is the engine's own behaviour (0.04 of the synthetic
audio got through, 97% of fake output blocks were silent, and the real speaker's rtmixer ring was empty at 2,489 device
callbacks). **Scenario F is the isolating measurement for Rust.**

### F. Rust engine with no Python in its audio path (ONE run, Rust only)

Rust's own fake backend feeds 32 lanes from a 2 ch sine and drains a 2 ch output on the wall clock (all in Rust threads); Python
only reads four 24 kHz taps. 30 s idle, then 30 s with the tight busy thread.

| | idle | busy thread |
|---|---|---|
| Speaker underruns / resyncs / dropped frames | 0 / 0 / 0 | 0 / 0 / 0 |
| Every engine glitch counter | 0 | 0 |
| Tap samples vs expected (4 taps) | 1.0004 | 1.0000 |
| Tap longest read gap, ms | 18 | 68 |
| Process CPU % | 18.7 | 102.7 (the busy thread is about 98 of that) |

The engine itself does not glitch when Python is saturated; only Python-side reads slow down (the 68 ms gap is the tap reader waiting for the GIL).

### Meter sanity

* Lane meters of the 31 synthetic sine lanes (expected -33.47 dBFS RMS): both engines within 0.04 dB of expected, every lane (mean of 1 Hz readings).
* Output meters in B (expected -58.6 dBFS RMS for the speaker; the bus mixes 31 sines at -40 dB): Rust read 1.5 to 2.7 dB under the expected
  value. **That was the benchmark's averaging, not the meter**: it took the plain mean of 10 ms block RMS readings, and the 31 in-phase sines
  (all multiples of 30 Hz) swing block RMS from -17 to +5 dB around the level, which puts that mean about 2.9 dB low (0 dB at blocks of 33 ms
  or more). Averaged as power (`sqrt(mean(rms^2))`, now what `bench.py` does) the meter and a capture of the output both read the expected
  -58.55 dBFS within 0.1 dB: `cargo test --test meter_level`. The Python
  bus' meters are floored at -60 dBFS by `audio.dbfs`, so 40 to 60% of its readings of these outputs were exactly the floor and
  **a Python output-level check is not possible at this level**. The -20 dBFS tone in C is the check both can pass (above).
* Mic: the tap's RMS and the lane meter's RMS were within a few dB (1 Hz sampling of a live room is noisy; not a precise check).

### Memory

Python's baseline after imports is about 91-98 MB (the whole `app` package), Rust's about 41-45 MB. After the heavy configuration
runs: Python 143 MB, Rust 264 MB. **Rust's RSS grew by about 149 MB during the 60 s window in every B/C/D run** (Python grew 33 MB).
It was neither a leak in the audio path nor a ramp that settles: see "After CPU and memory fixes" below.

## After CPU and memory fixes (Rust only, scenario B, two 60 s runs)

The Python engines were not rerun; the Python columns are the existing medians from B above. Raw runs: `results/raw/rust-B-after1.json`,
`rust-B-after2.json` (RSS once a second from the window start in `rss_series_mb`). Same machine and harness as above, wheel rebuilt with maturin
and force-reinstalled into the repo `.venv`.

**What was wrong.**

1. CPU: every (lane, output) pair ran a 64-tap rubato sinc resampler with a ratio of about 1.000 +- 0.5% to follow the output clock (31 + 3 x 32
   = 127 of them in B, each rebuilt per block with a ramped ratio change). The rate conversion for a 44.1 kHz input device was already done once
   per input device (all channels together), and the one for a non-48 kHz output once per output, so only the per-lane follower was wasteful.
2. Memory, two causes, only one of them in the engine:
   * **Not a leak.** `read_tap` allocated `Vec::with_capacity(max_frames)` for every read. The bench asks for 24,000 frames and gets a few hundred,
     so each returned numpy array was backed by a 96 KB buffer, and the bench keeps every chunk of its three synthetic taps for the continuity check.
     That is what grew by about 150 MB a minute (RSS grew in a straight line, 2.5 MB/s, and did not change when the rings were shrunk). The Python bus
     keeps the same chunks at their real size. Found with `vmmap` (the growth was all `MALLOC_MEDIUM`) and a Python loop over `push`, `pull`,
     `read_tap` and `status` that showed none of them leaking per call. A Relay caller that does not keep its reads would not have seen it, but a
     buffer sized by the caller's ceiling instead of by the data is wrong either way.
   * Rings were bigger than needed: 2 s (384 KB) per (lane, output), all touched as the write position wraps, 49 MB for B.

**What changed** (all under `native/relay-bus/`):

* `output.rs`: the per-lane follower is a four-point cubic (Catmull-Rom) interpolator with a fractional read position, 1/ratio input frames per
  output frame. Sinc stays where there is a real rate conversion (a device not at 48 kHz). Mixing is channel-major (each cell is a contiguous
  multiply-add that vectorises, zero-gain cells skipped, interleaved once per block). Lane rings are 16,384 frames (341 ms), above the resync
  threshold (about 130 ms at 60 ms latency) with room.
* `tap.rs`: `read` starts at 2,048 frames of capacity and `shrink_to_fit`s the result, so a kept read holds about its own samples.
* Tests: `tests/memory.rs` installs a counting global allocator (wrapping the no-alloc checker) and runs the 32-lane, four-output configuration on the
  fake backend for 10 simulated minutes with a reconfiguration every 30 s (outputs added and removed) and four 24 kHz taps read throughout, then asserts
  the heap is within 256 KB of where it was after warm-up (it measured 0 KB growth, 12.3 MB held). A second test keeps 600 tap reads and asserts they
  hold about their own samples (1.1 MB for 1.1 MB of samples; it was 13.5 MB). `examples/cpu.rs` times the same configuration on the fake backend:
  30 simulated seconds in 0.87 s, 2.9% of one core for the whole engine.
* Considered and not done: one shared ring per lane with a read position per output. With rings at 64 KB and the writes a plain copy, the copy
  is not where the time goes any more, and sharing would turn a consumer that falls behind from "loses its own frames" into "corrupts everyone's
  read position". Meters and the 24 kHz taps showed nothing worth changing in the numbers below (tap readers 5.6% of a core, which is mostly the
  bench's own numpy work).

| Scenario B, Rust, 60 s | before (3 runs, median and range) | after, run 1 | after, run 2 | Python (rtmixer) | Python without rtmixer |
|---|---|---|---|---|---|
| Process CPU, % of one core | 54.7 (54.2-54.9) | **30.0** | **29.2** | 52.5 | 59.3 |
| RSS at end, MB | 264 (234-265) | **104.2** | **104.4** | 143 | 100 |
| RSS growth over the 60 s window, MB | 149 (118-150) | **38.4** | **38.5** | 33 | -11 |
| Speaker underruns | 0 (0-93) | 0 | 0 | 19 | 222 |
| Fake outputs underruns (3 outputs) | 0 (0-62) | 0 | 0 | 93 | 76 |
| Resyncs; silent output blocks; input frames dropped | 0; 0; 0 | 0; 0; 0 | 0; 0; 0 | 0; 0; 0 | 0; 0; 0 |
| Tap samples vs expected (four taps) | 0.9999 | 1.0005, 1.0000, 1.0003, 1.0003 | 1.0006, 1.0003, 1.0003, 1.0003 | 1.0000 | 1.0000 |
| Tap dropped / waveform discontinuities | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Tap longest read gap (worst of the four taps), ms | 51 (37-59) | 37 | 44 | 50 | 47 |
| Input driver lateness p99 / max, ms (harness check) | 4.6 / 14 | 4.4 / 7.9 | 4.3 / 9.1 | 5.3 / 8.8 | 4.8 / 6.4 |

RSS during the window, MB, every 5 s from its start (run 1 / run 2): 65.8 / 65.9, 68.7 / 69.0, 71.8 / 72.2, 75.1 / 75.4, 78.6 / 78.5, 81.6 / 81.7,
84.8 / 84.8, 88.1 / 88.2, 91.3 / 91.6, 94.5 / 94.6, 97.7 / 97.8, 100.9 / 101.2 (104.2 / 104.4 at 60 s). **It does not plateau in the harness**: it
still rises 0.64 MB/s. That is the harness keeping what it reads: the three kept 24 kHz taps are 17 MB of float32 in 60 s, plus the per-tick
lists in `bench.py`; the Python engine's 33 MB is the same harness. The engine's own heap is flat (the memory test, and RSS going from
173 MB over imports to 62 MB over imports with the same harness). I did not run a longer bench to show a harness-free plateau.

CPU by thread group, % of one core (run 1 / run 2): bench drain threads, which run the Rust mix inside `pull` (was 33.5): **13.1 / 12.7**;
bench generators (input pushes) 7.7 / 7.4; tap readers 5.7 / 5.6; native-other 3.5 / 3.4; Rust threads 0.06 / 0.05. The harness's own Python work (generators,
tap numpy, drain inspection) is now most of the 30%.

Two earlier runs, made with the CPU fix but before the tap buffer fix, are kept in `results/raw/after-cpu-only/` and are not in the table:
CPU 28.9 and 22.9%, RSS growth 149.8 and 148.1 MB (the growth was not changed by the CPU fix, which is how it was shown not to be the rings or
resamplers), speaker underruns 0 and 70, fake outputs 0 and 77. The second one's generator lagged up to 37 ms, so this looks like the same
harness-timing cause as the sporadic underruns above; the two final runs and the 3 before-runs without them do not settle whether the cubic
interpolator makes them more or less likely (0 in 2 of 2 now, none in 1 of 2 intermediate).

Drift: `cargo test --test drift` (an hour of simulated time against clocks 3000 ppm fast and slow): 0 underruns, 0 resyncs, worst instantaneous
fill deviation 1.33 ms (limit 5), learned drift 1.00300 / 0.99700 / 1.00000. All tests, `cargo clippy --all-targets -- -D warnings` pass.
Output meters read 1.6 to 2.6 dB under the expected level in both runs (1.5 to 2.7 before) because of how the bench averaged them, not the meter (see Meter sanity; the numbers in the JSON files predate the fix), and
the 31 synthetic lane meters were within 0.03 dB. Not measured: the cubic interpolator's frequency response against the sinc (the bench's synthetic
taps are taken from the lane, before the follower, so they say nothing about it); a spectral comparison of an output would be the check.

## How each scenario was made fair

* **Same config dict.** `build_config()` produces the `audiobus.normalize()` shape for both (asserted equal for Python). Lane 1 is the
  real default microphone. Lanes 2-32 are the 31 channels of four synthetic 8 ch devices (`d0` 48 kHz, `d1` 44.1 kHz, `d2` 48 kHz,
  `d3` 44.1 kHz), so 15 lanes need resampling. Every device carries a sine (30 mV peak) at a distinct frequency per lane. Output
  `spk` is the real default speaker, 2 ch; `f1..f3` are 8 ch outputs that are not devices. Matrix: every lane to every channel of
  each fake output, and lanes 2-32 to the speaker, all at -40 dB.
* **No cell from the microphone to the speaker.** The brief asked for every lane to every output channel and also for the mic never to
  reach the speaker; the safety rule won. Lane 1 feeds the three fake outputs only. The code asserts this and that the speaker's
  worst-case peak (31 sines summed in phase) is under -40 dBFS (it is -40.6). The only louder signal is C's -20 dBFS tone.
* **Synthetic inputs.** One Python thread (the same code for both) pushes each device's next 20 ms block every 20 ms on an absolute
  schedule: the Python bus gets it through the intake's `deliver` (what a PortAudio reader thread calls), the Rust bus through
  `push`. So in both engines the input callback work runs on that thread. Lateness of that schedule is in the tables (p99 about 5 ms for all).
* **Fake outputs.** Python bus: a `LaneSink` test double whose thread drains its ring at 48,000 frames per wall second and inspects every
  block. Rust: a Python thread per output calling `pull()` at the same pace and inspecting each block. The two pieces of work are the
  same mix and meter, but they sit in different places: in Python it runs in the bus's own threads; in Rust it runs inside `pull()` on the
  caller's thread (the "drain" group). The Rust fake backend was not used because it only offers 2 ch devices with fixed names.
  CPU therefore compares whole-process cost, which includes the harness threads on both sides (about 5 to 6% each for the generator).
* **Taps.** Python: `audiobus.LaneSource` on the lane, a reader thread that resamples to 24 kHz with soxr as `AudioCapture`'s pump does.
  Rust: `open_tap(rate=24000)` and `read_tap` in a thread. Synthetic taps are checked for continuity (a sine obeys
  x[n] = 2cos(w)x[n-1] - x[n-2]; any dropped or inserted chunk breaks it): zero discontinuities in every A-D run.
* **Counting.** Every run is its own process. 5 s of settling is not counted; counters are deltas over the 60 s window. CPU is
  `getrusage` over the window, with a per-thread breakdown from `proc_pidinfo`. Python's per-lane-per-tick underrun counter and
  Rust's per-episode counter are not the same unit: compare "zero versus not zero", not the magnitudes.
* **Temp data.** Python-bus runs set `RELAY_CONFIG` to a temp directory, so config and database writes stay out of the real data directory.

## Findings that change how to read this (surprises)

1. **The Python bus with rtmixer 0.1.7 can go silent permanently, and nothing notices.** An rtmixer `play_ringbuffer` action ends
   the first time the ring is empty at a device callback, and the ring is never read again, while `stream.active` stays true, so
   `PortAudioLaneSink.alive()` is true and the supervisor never reopens it. This happened at stream open in 9 of 10 `python` runs
   in A-D (and mid-run in two: 2 times in an A run, 7 in a C run that had a 510 ms stall) (the first callback arrives before the output thread's first write), which left the speaker mute until the harness re-armed it
   (the harness re-arms and counts it; without that, the speaker queue sat at exactly its 30 ms target forever, which is how I
   found it). In scenario E it happened 2,489 times in 60 s. Also rtmixer's underflow counter did not count these. This is a bug in
   `app/audio.py` (not changed here) that Relay would have independent of this spike. Same for the mic Recorder on overflow (0 observed).
2. **The Rust bus is not cheaper than the Python bus in the heavy case.** B: Rust 54.7% of a core against Python 52.5% (59.3% without rtmixer).
   The Rust cost is dominated by the per-lane, per-output sinc resampler of the drift loop (32 lanes x 3 outputs + 31 for the speaker, always
   running at about 1:1). Python's drift follower is linear interpolation in numpy. A cheaper interpolator, or a bypass when the ratio is
   within a few ppm of 1, would be the obvious fix. In the light case Rust wins clearly (4.7% against 11.5% and 8.2%).
3. **Rust had sporadic speaker underruns in 3 of 7 heavy runs** (B: 93, C: 31, D: 83 per-lane events in 60 s), and
   none in the other four. Python's speaker had some in 5 of 7 (median 19 in B) and its fake outputs in every one. The generator thread lagged up to 42 ms at the worst
   in the Rust run with 93, so a harness cause is likely, but I did not prove it.
4. **Under load, Python's alert reaches the device about 25 to 30 ms later than Rust's.** Both are far inside the 500 ms requirement.
5. Python's meters stop at -60 dBFS, so quiet outputs read as the floor.

## Caveats

* One machine, one OS version, one afternoon, other apps open. Spread comes from 3 runs only (A, B, C). D, E and F are single runs: treat
  their numbers as a demonstration, not as measured tails.
* Scenario G (a bursty Python load, 2 ms of work per 10 ms) was written in `bench.py` (`--scenario G`) but **not run**, for time.
* The real mic and speaker are the only real devices, so there is no real multichannel hardware, and no network (Dante) input.
  The speaker device callback cannot be timed from Python for either engine.
* The Python bus opens the speaker at 48 kHz and lets CoreAudio convert to the device rate (44.1 kHz); Rust opens it at the device's
  rate and converts itself. That conversion cost sits in the "native" group for Python and in Rust's callback for Rust.
* E's synthetic feeding is penalised for Rust by the GIL (explained above). F is the fair Rust number for that scenario and has no Python counterpart.
* Spurious numpy/Accelerate floating-point warnings from threaded matmul in the Python bus are silenced in the bench; the products were
  checked against float64 and are correct.
* D2/D3 runs of scenario D (two extra repeats of each engine) exist in `results/unused/` and are not in the tables, per the change of plan.
