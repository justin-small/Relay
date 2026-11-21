---
title: "The Python bus now runs on the Rust wheel: Python reads network sources and drives network senders, and lane consumers run on their own threads"
author: Justin
date: 2026-11-21
branch: rust-bus-wire-601
cost: { minutes: 16, tokens: 195813 }
id: l-50036ce6
scope: repo
status: active
src: agent
touches:
  - native/relay-bus/crates/relay-bus/src/output.rs
  - ".github/workflows/release.yml"
  - ".github/workflows/tests.yml"
  - native/relay-bus/deny.toml
  - requirements-wheel.in
  - requirements-wheel.txt
  - security/bandit-baseline.json
  - tests/run_security.py
  - app/audiobus.py
---

## Decided
Justin, 2026-11-21, issue 601 (wiring PR): app/audiobus.py is a facade over the relay_bus wheel and the old Python mixing is deleted, with no fallback. Sound devices (source/output "portaudio") belong to the Rust engine; any other source (AES67, talkback) is read in Python and handed over with push, and any other output (AES67 sender) is fed from the engine with pull. Lane consumers (metering, the capture, the mix report) read a tap on a thread of their own. The wheel is required everywhere: run_all.py and every CI job that imports the app build it with tools/build_bus_wheel.py.

## Tried and ruled out
Keeping Lane.push for tests: the engine has no way to put audio on an unpatched lane, so tests patch a lane to an unregistered source and push through the engine (tests/_buslane.py).
Reading taps in 960-frame blocks: a burst overflowed the metering queue; lanes are read up to 4800 frames at a time.

## Watch out
A PortAudio device name saved in config may not match the name cpal uses on Windows or Linux; not tested.
Consumers now run behind the audio, so tests wait for them with wait_for; a stopped sender must be stopped before the engine drops its output, or it pulls from a gone output.
Not run: the Windows and Linux benchmark, real-device unplug and replug, the notarized-build microphone permission.

In the code:
app/audiobus.py (Bus, Lane, _Reader, _PyIntake, _PyOutput), tools/build_bus_wheel.py, tests/_buslane.py, tests/run_all.py.
