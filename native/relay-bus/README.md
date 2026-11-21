# relay-bus: Rust audio bus spike (issue 599)

This is research for issue 599: should the realtime half of Relay's 32-lane audio bus (`app/audiobus.py`) be a
Rust core instead of Python and numpy? The Python bus works, but its outputs resample with numpy in a thread that
shares the GIL with everything else, it copies a lot of arrays, and it cannot promise that a block is ready on time.
The question this spike answers is whether a Rust core can keep the same semantics, be provably free of allocation
on the realtime path, and be small enough to live with. It does not decide that. Phase 1 is the engine and its tests;
the decision comes after phase 2 puts real devices and Python in front of it.

Nothing in `app/` or `tests/` uses this yet. It builds on its own:

```bash
cd native/relay-bus
cargo test
cargo clippy --all-targets -- -D warnings
```

`target/` is ignored by git.

## What is here

A Cargo workspace of three crates. `crates/relay-bus` is the engine, plain Rust with no devices and no Python: it opens
streams through a `Backend` trait, and its own implementation is a deterministic fake that the tests drive on a virtual
clock. Phase 2 added `crates/relay-bus-cpal` (the cpal `Backend` and the device-change watcher) and
`crates/relay-bus-py` (the PyO3 module, built with maturin). The table below is the engine crate.

| File | What it holds |
| --- | --- |
| `config.rs` | `Config`, `LaneSpec`, `OutputSpec`, `Cell`: the shape of Python's `normalize()` output; `db_to_gain` |
| `backend.rs` | the `Backend` and `Stream` traits (open an input or output with a callback) |
| `fake.rs` | the fake backend: virtual clock, per-device rate error, unplug and fault injection, output capture |
| `engine.rs` | `Engine`: apply, supervise, push, pull, inject, taps, status. Everything non-realtime |
| `input.rs` | what runs in an input callback: rate conversion, channel pick or mix, lane meters, fan-out |
| `output.rs` | what runs in an output callback: per-lane drift loop, matrix mix, clip injection, output meters |
| `rt.rs` | the two queues to a realtime thread (commands in, discards out) |
| `tap.rs` | non-realtime readers on a lane, with an optional output rate |
| `meter.rs`, `health.rs`, `status.rs` | meters, glitch counters, the `status()` types |

## Design

### The rule

A realtime callback never allocates, frees, locks or waits. Everything it needs is built by the engine beforehand
and moved to it, and everything it retires is moved back. Two kinds of queue make that work, both SPSC rings from
`rtrb`. This is the pattern in Splitwave's `input_bridge.rs`.

* A command queue, engine to callback. Commands carry finished objects: a boxed tap, a boxed sink (a ring's
  producer end), a boxed `LaneRx` (a ring's consumer end plus its resampler), a pre-built clip, a new gain array.
  The callback drains the queue at the top of each block and installs them. Boxes are moved as pointers; collections
  on the realtime side are created with their maximum capacity (16 consumers per lane, 32 taps, 16 clips) so
  adding one never reallocates.
* A discard queue, callback to engine. A replaced tap, a removed ring, a finished clip is pushed here and dropped by
  `Engine::apply` and `supervise` on the engine's thread. If the queue were ever full the object is leaked and
  `glitches.discard_overflow` counts it, because freeing on the realtime thread is the worse failure.

### CPU and memory notes (after the phase 3 benchmark)

* **The drift follower is a cubic interpolator, not a sinc resampler.** Each (lane, output) pair holds four samples of history and a fractional read
  position that advances 1/ratio input frames per output frame; the PI loop sets the ratio, which never leaves 1 +- 0.5%. In the 32-lane,
  four-output case that is 127 followers, and the sinc version was most of the 54.7% of a core measured in phase 3 (30% after, with the harness's
  own Python work most of it). Sinc remains where a real rate conversion is needed: once per input device not at 48 kHz (all channels together) and
  once per output not at 48 kHz.
* **The mix is channel-major.** Each (lane, channel) cell is one contiguous multiply-add over a block, cells at zero gain are skipped, and the block
  is interleaved once at the end.
* **Rings are sized for what they hold.** A lane ring is 16,384 frames (341 ms): the resync threshold is about 130 ms at the default latency. Per-consumer
  rings (one per lane per output) stay; a shared ring with a read position per output was considered and not done, because a slow consumer would then
  share its position with the others. Taps keep their 4 s default capacity (it is the reader's allowance for being slow, one ring per tap).
* **`read_tap` returns exactly its samples.** It used to allocate the caller's ceiling (`max_frames`), so a caller that kept its reads held 96 KB per
  read. That, not a leak in the engine, was the 150 MB a minute in phase 3. `tests/memory.rs` checks it, and that the heap stays flat across ten simulated
  minutes of reconfigurations. `cargo run --release --example cpu` times the heavy configuration on the fake backend.

### Threads

* One realtime thread per open device stream (cpal will provide these in phase 2). An input callback owns an
  `InputCore`; an output callback owns an `OutputCore`.
* External inputs (`push`) and pull outputs (`pull`) are the same cores, run on the Python caller's thread under a
  mutex. They are not realtime, but they take the same path, so what the tests prove about one holds for the other.
* The engine is behind one mutex and is only ever called from non-realtime threads. Taps have their own lock so a
  blocking `read_tap` does not hold the engine.

### Data path

```
input device -> [InputCore] --pick/mix, meter--> ring per consumer --> [OutputCore: LaneRx per lane] -> matrix -> device
 (or push())     rate -> 48k    (fan-out)        (rtrb SPSC)            drift loop + resampler          clips added last
                                                                 \--> tap ring --> read_tap() (resampled to the tap's rate)
```

* **Lanes.** 32 mono lanes at 48 kHz. An input is opened once however many lanes use it. A device not at 48 kHz is
  converted once, all channels together, with rubato's async sinc resampler; each lane then takes one channel or the
  average of all of them (a channel past the end means the last one, as in Python).
* **Fan-out.** Each lane has any number of consumers and each has its own ring, written by the input callback. A
  consumer that falls behind loses frames (counted per input, per consumer and globally); it never blocks the
  writer. Outputs and taps are both consumers.
* **Outputs.** An output of 1 to 32 channels mixes `sum(lane x gain)` per channel at 48 kHz in blocks of 256 frames,
  converts to the device rate if needed, and fills whatever block size the device asks for. Gain is dB from -90 to
  +24; at or below -90 dB it is exactly zero. A gain change ramps linearly across one block, so there are no steps.
* **Meters.** Level (RMS), peak with a 20 dB per second decaying hold, and a clip light held for 3 seconds, for
  every lane and every output channel. The realtime side publishes them as f32 bits in atomics; `status()` reads
  them. Time for the decay is audio time (samples counted), so the realtime code never reads a clock.
* **Taps.** `open_tap(lane, {rate, capacity_ms})` and `read_tap(tap, max_frames, timeout)`. A tap is a consumer with
  its own ring, and the conversion to its rate (for example 24 kHz for speech to text) happens in `read_tap`, on the
  reader's thread, once. A slow reader gets `dropped` on its next read.
* **Pull outputs and external inputs.** An output or lane whose `output` / `source` is not `"portaudio"` (the name
  for "a device the backend opens") is a network endpoint. Python feeds an input with `push(input_id, interleaved,
  channels, rate)` and drains an output with `pull(output_id, frames)`, paced by its own clock. The drift loop runs
  on it exactly as it does on a device.

### The drift loop

Devices run on their own clocks, so an output follows its own. For every lane it reads, `LaneRx` holds that lane's
ring at a target fill (half the output's `latency_ms`, but never under three blocks) by adjusting the ratio of a
mono rubato resampler running at about 1:1:

* once per block, measure the ring fill, smooth it with a 0.5 s filter (the raw value has a sawtooth of an input
  block or two that must not reach the ratio),
* `trim = -(KP * error + integral)`, with `integral += KI * error * dt`, error in seconds of audio,
  `KP = 0.7`, `KI = 0.12` (critically damped, natural frequency 0.35 rad/s),
* clamp the trim to +-0.5%, and call `set_resample_ratio_relative` with ramping on, so the pitch glides.

The integrator is the learned clock offset. `status().outputs[].drift` reports it (1.003 means the output's clock is
0.3% fast against its lanes); `ratio` is the instantaneous value, which wobbles around it by the proportional term.

It is per lane, not per output, because two lanes fed by two input devices have two different errors against the
same output, and one ratio can only fix one. A lane added to a running output starts its integrator from the output's
current drift instead of 1.0.

Underrun and resync handling: a lane waits ("priming") until its ring reaches the target, then runs. If the ring
cannot supply a block it counts one underrun and primes again (so a dead input is one underrun, not one per block).
If the ring holds far more than the target, the reader had stalled: it skips ahead and counts a resync. When a lane
(re)starts, one block is used to warm up the resampler's filter history and the next is faded in, so it does not
begin with a step.

### Clip injection

`inject(output_id, channels, samples, rate)` converts the clip to the output's device rate on the calling thread,
boxes it, and sends it through the command queue. The callback adds it to the device block it is filling,
after the latency buffer and the resampler, so an alert is heard at the output's next block (tested with the
maximum 500 ms latency setting, where the clip must still be in the very first block). A finished clip is returned on
the discard queue. `stop_injects` clears them at the next block.

### Live reconfiguration

`apply(config)` takes the same shape as `normalize()` and changes only what differs. A matrix-only change sends new
gains (ramped) and adds or removes the lane rings it needs; no stream is reopened and no drift state is reset. A
removed cell keeps its ring for a quarter of a second so the gain can fade on live audio, and the next `supervise()`
lets it go. A changed device, channel count or latency reopens only that input or output.

Internally each (lane, consumer) pair is a `Wire`, stamped with the generation of the input and consumer it was made
for. `rewire` adds missing wires and removes stale ones; a stream that dies and comes back gets a new generation, so
it is rewired with no special case.

### Supervision

Streams report errors through `Stream::error()`. `supervise()` (call it about every 0.5 s) closes a failed stream,
marks it `waiting` with the error text, and reopens it with backoff (0.5 s, doubling to 8 s). An input that is "open"
but has delivered no frames for 2 seconds is treated as lost, as in the Python bus. `status()` reports
open, waiting or closed with the error string.

## What phase 1 does and does not do

Done, with tests: everything above, against the fake backend.

Not done:

* No cpal backend, no PyO3 module, no change to `app/` (the first two are phase 2, below; `app/` is still untouched).
  In phase 1 the engine had never touched a real device.
* `last_input_age_s` is only as fine as the polling: it is computed from a frame counter whenever `status()` or
  `supervise()` runs, because the callbacks do not read a clock.
* Rate conversion runs on every channel of a device even if only one is used. Cheap enough at 2 channels, worth a
  mask for a 32-channel device.
* No spoken-language lane groups, recorder, or the `LaneSource`/`BusOutput` adapters from `audiobus.py`; those are
  Python glue over `open_tap` and `inject`.
* The drift loop's settle time on a large (0.3%) offset is about 15 seconds, with a peak fill error of a few
  milliseconds. Real clocks differ by tens of ppm and settle with far less. There is no fast-acquire mode.

## Tests

`cargo test` runs 33 engine tests and 2 cpal tests (the Python crate has none) in under 20 seconds, all deterministic: no wall-clock sleeps, no real devices.
The fake backend fires callbacks from a virtual clock, so an hour of audio is simulated in about 15 seconds (the
`[profile.test]` in `Cargo.toml` is optimised; debug assertions and overflow checks stay on).

| File | What it proves |
| --- | --- |
| `tests/matrix.rs` | sums and gains (dB to linear, the -90 dB mute floor, +6 dB), channel pick versus mix, a device opened once for many lanes, 44.1 kHz in and out keeps level and pitch, meters (hold, decay, clip), last-input age |
| `tests/meter_level.rs` | 31 in-phase sines through the matrix: the output meter's power average and a capture both match the true level (the benchmark's 1.6-2.6 dB offset was its averaging) |
| `tests/drift.rs` | one hour against an output clock 0.3% fast, one 0.3% slow, and one exact: zero underruns and resyncs after settling, fill within 5 ms of the target, the learned drift within 50 ppm of the true offset; two input clocks into one output; a fast input clock |
| `tests/inject.rs` | a clip is in the very next block at 500 ms latency, in the right channels, on top of live audio; clips add and end by themselves; stop cuts at the next block; a 24 kHz clip is converted |
| `tests/live.rs` | a new matrix mid-stream with no underrun, resync, drift reset or step; a removed cell fades; adding and removing lanes, outputs and taps through the queues; an output and an input that fail go to waiting and come back through `supervise()` with backoff; a hung input is declared lost; a pushed 44.1 kHz source reaches a device output; a pull output against a source 0.2% fast |
| `tests/tap.rs` | a 24 kHz tap gets continuous audio (no gap, click or level change); a tap on a 44.1 kHz input; a slow reader gets a drop count while another tap loses nothing; taps come and go; a tap opened before its lane is patched |
| `src/config.rs` | unit tests for dB conversion and validation |

**No allocation on the realtime path.** The `no-alloc-check` feature (turned on for tests by a self dev-dependency)
wraps every callback the fake backend fires in `assert_no_alloc`, and each test binary installs its allocator
(`tests/common/mod.rs`). An allocation or free inside a callback aborts the test process. Every test that runs
callbacks is therefore also an allocation test; `live.rs::adding_and_removing_lanes_and_outputs_goes_through_the_queues`
exercises every command type, 44.1 kHz conversion on both sides, injection and taps in one run. To check the guard is
live, put a `vec![0u8; 10]` in `OutputCore::render` and run any test: it aborts with "memory allocation of 10 bytes
failed".

## Phase 2: real devices and Python

Phase 2 puts the phase-1 engine in front of this Mac's real devices and behind a Python class. Nothing in `app/` or
`tests/` uses it; the wheel is installed into the repo's `.venv` by hand and is in no requirements file.

### What was added

| Where | What |
| --- | --- |
| `crates/relay-bus-cpal/src/lib.rs` | `CpalBackend` (the `Backend` and `Stream` traits on cpal 0.18) and `list_devices()` |
| `crates/relay-bus-cpal/src/watcher.rs` | `DeviceWatcher` and `ChangeSignal`: tells the engine when devices come and go |
| `crates/relay-bus-cpal/examples/watch.rs` | prints a line on every device change; for trying the watcher by plugging a device in |
| `crates/relay-bus-py/` | the PyO3 module `relay_bus._native`, `python/relay_bus/__init__.py`, `pyproject.toml` (maturin) |
| `python/smoke.py` | the hardware smoke test (below) |
| `crates/relay-bus/src/engine.rs`, `status.rs` | two small additions: `Engine::retry_now()` (make waiting streams retry at the next `supervise()` instead of after their backoff) and `OutputStatus::rate` (the device's rate) |

**cpal backend.** A device is chosen by name (exact name or id, then case-insensitive; `None` is the system default).
Streams use f32 when the device offers it, else i16 or i32, converted through scratch buffers allocated when the
stream is built, so the callbacks still do not allocate. An output asked for fewer channels than the device has leaves
the others silent. A stream error that means the stream is dead (device not available, backend error) is kept for
`Stream::error()`, which is how `supervise()` reopens it; cpal's advisory errors (xrun, reroute to the new default,
refused realtime priority) are counted in `status()["advisories"]` and otherwise ignored. cpal's `realtime` feature
(`audio_thread_priority`) is on for Windows and Linux. cpal has no such feature on macOS, where CoreAudio's callback
threads are already realtime. The device is opened at its own default rate, and the engine resamples to and from 48 kHz.

**Device change notification, per OS.**

| OS | What it uses | State |
| --- | --- | --- |
| macOS | CoreAudio HAL property listeners (`AudioObjectAddPropertyListener`) on the device list, the default input and the default output, through `objc2-core-audio`. It first sets `kAudioHardwarePropertyRunLoop` to NULL, because otherwise the HAL delivers to the main run loop, which a Python process does not run. | registered and `Bus.watch_method == "coreaudio-listener"`; a change actually arriving was **not** observed (see below) |
| Windows | polls cpal's device list and the two default names every 2 s | compiles (`cargo check --target x86_64-pc-windows-msvc`); never run |
| Linux | the same 2 s poll | never built or run (this Mac has no ALSA headers) |

An IMMNotificationClient callback is the right answer on Windows and was left out: it could not be built and tried
from this Mac. If the macOS listener cannot be registered the watcher falls back to the poll.

**Python.** `import relay_bus`; `relay_bus.list_devices()` and `relay_bus.Bus(backend="cpal" | "fake")`. Arrays are
numpy float32. Every call that can block releases the GIL (`py.detach`). No audio callback calls Python. A Rust thread
(`relay-bus-supervisor`) calls `supervise()` every 0.5 s and at once when the watcher fires (after `retry_now()`), so
Python does not have to.

| Call | Returns |
| --- | --- |
| `Bus(backend="cpal")` | `"fake"` runs the fake backend on the wall clock (2 channel 1 kHz sine, devices "default", "Fake Mic", "Fake Speaker"), for trying the API without a sound card |
| `apply(config)` | `None`; `config` is the dict `audiobus.normalize()` returns. `ValueError` for a bad config |
| `status()` | dict: `running`, `lane_rate`, `advisories`, `lanes[]` (each with `meter{rms,peak,clipping,clipped_samples}`, `state`, `error`, `last_input_age_s`), `inputs[]`, `outputs[]` (`state`, `error`, `rate`, `underruns`, `resyncs`, `drift`, `ratio`, `queued_ms`, `frames_out`, `meters[]`), `taps[]`, `glitches{}` |
| `supervise()`, `retry_now()`, `shutdown()`, `close()` | `shutdown` closes the streams; `close` also stops the Rust threads |
| `push(input_id, array, rate)` | array is (frames,) or (frames, channels) float32; `input_id` is `source:device` lower-cased |
| `pull(output_id, frames)` | (frames, channels) float32 at 48 kHz |
| `inject(output_id, channels, array, rate)` | channels count from 0; mono float32 clip |
| `stop_injects(output_id)` | |
| `open_tap(lane, rate=None, capacity_ms=4000)` | tap id |
| `read_tap(tap, max_frames, timeout_s=0.0)` | `(float32 array, dropped)` |
| `close_tap(tap)` | |
| `list_devices()` | `[{name, inputs, outputs, default_rate, is_default_input, is_default_output}]` |
| `watch_method` | `"coreaudio-listener"`, `"polling-2s"` or `"none"` |
| `notify_device_change()`, `fake_unplug(name, bool)` | try the supervise path without a device to unplug (the second is fake only) |

The wheel is `abi3` for Python 3.10 and later. The task asked for 3.11+; the repo's `.venv` is 3.10, so `abi3-py310`
is what lets it install there, and it covers 3.11 and up as well. The crate is `publish = false`. Its native module is
`relay_bus._native` (the Rust library is named `_native` because `relay_bus` is the engine crate's name).

### Build and install (macOS, this machine)

```bash
cd native/relay-bus
python3 -m venv .venv-build && .venv-build/bin/pip install maturin     # throwaway; ignored by git
.venv-build/bin/maturin build --release -m crates/relay-bus-py/Cargo.toml -o target/wheels
../../.venv/bin/pip install --force-reinstall target/wheels/relay_bus-0.1.0-cp310-abi3-*.whl
../../.venv/bin/python python/smoke.py
```

### On Windows (untested)

Needs Rust (rustup, MSVC toolchain), Python 3.10+ and nothing else: cpal uses WASAPI and needs no SDK. In PowerShell:

```powershell
cd native\relay-bus
python -m venv .venv-build
.venv-build\Scripts\pip install maturin numpy
.venv-build\Scripts\maturin build --release -m crates\relay-bus-py\Cargo.toml -o target\wheels
python -m venv .venv-run
.venv-run\Scripts\pip install numpy
.venv-run\Scripts\pip install --force-reinstall (Get-ChildItem target\wheels\relay_bus-*.whl).FullName
.venv-run\Scripts\python python\smoke.py
cargo run -p relay-bus-cpal --example watch     # then plug and unplug a USB or Bluetooth audio device
```

### On Linux (untested)

Needs Rust, Python 3.10+ with `venv`, a C compiler, `pkg-config` and the ALSA headers
(`sudo apt install libasound2-dev pkg-config build-essential python3-venv`; Fedora: `alsa-lib-devel`). cpal reaches
PulseAudio and PipeWire through ALSA's plugins, so the default device is whatever ALSA's `default` is.

```bash
cd native/relay-bus
python3 -m venv .venv-build && .venv-build/bin/pip install maturin numpy
.venv-build/bin/maturin build --release -m crates/relay-bus-py/Cargo.toml -o target/wheels
python3 -m venv .venv-run && .venv-run/bin/pip install numpy
.venv-run/bin/pip install --force-reinstall target/wheels/relay_bus-*.whl
.venv-run/bin/python python/smoke.py
cargo run -p relay-bus-cpal --example watch     # then plug and unplug a USB audio device
```

On either OS, `python python/smoke.py --backend fake` checks the Python side with no sound card, and
`--drift-cell` (see the docstring) adds the microphone-to-speaker cell at -80 dB to read the clock offset between them.
Paste the whole output back; the numbers worth comparing with the table below are the tap rate and RMS, the
inject-to-meter time, underruns and resyncs, the CPU line and the drift log.

### What was run on this Mac (real devices)

MacBook Pro Microphone (1 channel, 48 kHz, default input), MacBook Pro Speakers (2 channels, **44.1 kHz** at the time,
default output), Python 3.10.11 arm64 in the repo `.venv`, `python/smoke.py` with default arguments, 30 s of running.
Microphone permission was already granted to the process; the tap heard real audio (RMS -36 dBFS from the room).
The microphone was never routed to the speaker. Numbers from one run (a second and third run agreed within the noise):

| Measurement | Result |
| --- | --- |
| `apply()` on the default input and output | returned in 103 ms with both streams open |
| 24 kHz tap on lane 1, 5 s | 120239 samples (24024 Hz over the 5.00 s wall window), RMS 0.015145 (-36.4 dBFS), peak 0.1049, 2.2% exactly-zero samples, **0 dropped** |
| 1 kHz tone at -20 dBFS, 1 s, `inject()` to both speaker channels | the `inject()` call took 2.0 ms; the output meter rose **3.4 ms** after `inject()` started (earlier runs: 6.1 and 8.5 ms); peak seen 0.1000, as sent |
| Soak, 30.01 s | output **underruns 0, resyncs 0**; input dropped 0; tap dropped 0; every global glitch counter 0; 0 advisory cpal errors |
| CPU of the Python process over those 30 s | **1.17 s of CPU, 3.9% of one core** (earlier runs 2.9 to 3.7%). This includes the Python test script's own work: reading the tap, polling `status()`, pushing 100 blocks a second |
| Speaker clock against this computer's clock, learned by the drift loop on a quiet pushed source (lane 2 into the speaker at -80 dB, about -110 dBFS) | converged from +476 ppm at 6.5 s and +214 ppm at 10 s to +10 ppm at 15 s, then +28, +9, +13 ppm at 20, 25 and 30 s; ring held at 30.3 ms against a 30 ms target |
| Bad device name, then the right one again (output and input) | each goes to `waiting` at once with the error `no output device named "..."`; reopened by `apply()` with audio flowing 10 ms later |

What those numbers do and do not say.

* The drift loop was exercised on a real device clock, but not against the microphone's: that would need a matrix cell
  from the microphone to the speaker, which the test must never have. The built-in microphone and speakers are also
  likely to share one clock, so a mic-to-speaker offset here would not mean much. The pushed source is paced by Python's
  `time.sleep`, so the +-30 ppm wobble in the learned drift is mostly that pacing, not the speaker. Read it as "the loop
  locks on a real device and holds the fill with no glitches", not as the speaker's true error.
* Without any source routed to it (lane 1 has no cell), the output's underruns are trivially zero. The lane-2 source is
  what makes the zero meaningful. A cell at -90 dB or less is exactly zero gain and the output then ignores the lane
  (its ring is not read and no drift is learned), which is why the quiet source is at -80 dB.
* The speaker ran at 44.1 kHz, so the engine converted 48 kHz lane audio to 44.1 kHz on the way out, and the injected
  tone came out at full level and pitch. The first listing in an earlier run showed the speaker at 48 kHz; the device's
  rate is whatever macOS has it at, and cpal is asked for exactly that.
* "Time from `inject()` to the meter rising" is measured by polling `status()` from Python, so it includes up to about a
  millisecond of polling. It is a bound on the time to the meter, not on when the sound reaches the air: the output
  block, CoreAudio's buffer and the DAC add to that and none of them was measured.
* Recovery here is `apply()` reopening a device whose configured name changed, not the supervisor noticing a lost
  device.

### Unplug and replug

The built-in devices cannot be unplugged, so two things were run instead.

1. On real devices, the configured device was changed to a name that does not exist and back (row above). That tests
   the open-fails path and that a config change recovers; the time to recover is the time to reopen.
2. On the fake backend through Python (`smoke.py --backend fake`, wall clock), a device was unplugged and plugged back
   in: with no change event the supervisor reopened it **0.90 s** after the device returned (backoff: 0.5 s, then 1 s);
   with `notify_device_change()` (what the watcher does) it was open again **2 ms** after.

A real unplug needs a USB or Bluetooth device: run the `watch` example and `smoke.py`, unplug it while `status()`
shows the stream open, and plug it back in. That is the test that matters for the watcher, and it has not been run.

### Not tested, or uncertain

* **Windows and Linux: nothing has run.** Windows type-checks and is clippy-clean (`cargo check` and `cargo clippy
  -D warnings --target x86_64-pc-windows-msvc` for the cpal and Python crates). Linux was not even built here (no ALSA
  headers). WASAPI shared mode and ALSA both have their own habits (a device that offers only i16, a fixed period size,
  ALSA listing dozens of pseudo-devices that `list_devices()` and the 2 s poll will probe); the i16 and i32 conversion
  paths and the "device has more channels than asked" path are written but were never exercised, because the Mac's
  devices are f32 and exact.
* The macOS listener registers (status 0 on all three properties) but **no notification was seen to arrive**: changing
  the default device or adding a device on this Mac means changing the user's system audio settings, which this test
  did not do. The run-loop workaround is from Apple's documentation of the HAL, not observed here.
* If the default device changes while a stream opened on "the default" is running, cpal reroutes it and reports
  `DeviceChanged`, which is counted and ignored. The engine does not learn that the rate or channel count may have
  changed. A stream opened on the default should probably be reopened when the default changes.
* A tap or `status()` poll is not realtime-safe code, but nothing checks that the Python side keeps up: a slow Python
  reader just loses tap frames and gets `dropped`.
* The Python module has no automated test (the user asked for no files under `tests/`); `smoke.py --backend fake`
  covers the API on any machine.
* The `-90 dB is exactly zero, so the lane is not read` behaviour is phase-1 design and matches the Python bus's
  intent, but it means a muted cell does not keep a lane's drift state warm.

## Crates

All permissive: `rtrb` (MIT or Apache-2.0), `rubato` and `audioadapter-buffers` (MIT), `assert_no_alloc`
(BSD-1-Clause, test and feature only). No code is copied from Splitwave or CamillaDSP; the structure follows their
ideas (Splitwave's command and discard queues and ring-fill-to-ratio loop and glitch counters; CamillaDSP's PI rate
controller driving rubato's `set_resample_ratio_relative`), written from scratch.

Phase 2 adds, all permissive and none copied: `cpal` 0.18.2 (Apache-2.0), `objc2-core-audio` 0.3 (Zlib OR Apache-2.0
OR MIT, macOS only), `pyo3` 0.30 (MIT OR Apache-2.0), `numpy` (rust-numpy) 0.30 (BSD-2-Clause), and their dependencies:
`coreaudio-rs`, `objc2`, `block2`, `dispatch2`, `mach2`, `ndarray`, `matrixmultiply`, `libc` and friends (MIT,
Apache-2.0, Zlib or BSD, in various combinations), `alsa` and `alsa-sys` (Linux; MIT or Apache-2.0), the `windows` crates
(Windows; MIT or Apache-2.0), and `audio_thread_priority` (MPL-2.0, Windows and Linux, the `realtime` feature). maturin
is a build tool in a throwaway venv (MIT or Apache-2.0). There is no GPL, LGPL or AGPL in the macOS, Windows or Linux
dependency trees (checked with `cargo metadata` for each target).
