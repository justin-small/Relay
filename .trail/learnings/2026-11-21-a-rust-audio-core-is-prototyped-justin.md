---
title: "A Rust audio core is prototyped as a spike before the bus rework, and the bus rework waits for its numbers"
author: Justin
date: 2026-11-21
branch: rust-bus-spike-599
cost: { minutes: 22, tokens: 112484 }
id: l-50d79350
scope: repo
status: active
src: agent
touches:
  - app/modules/aes67/__init__.py
  - app/modules/aes67/output.py
  - app/modules/aes67/routes.py
  - app/modules/aes67/sender.py
  - app/modules/aes67/service.py
  - app/modules/aes67/static/aes67.js
  - app/modules/aes67/templates/aes67.html
  - docs/modules.md
  - tests/test_aes67.py
  - tests/test_auth.py
---

## Decided
Justin, 2026-11-20, after researching existing audio systems for issue 599: prototype a Rust core for the 32-lane bus before building 599.
- It is a spike: a pure-Rust engine crate plus a PyO3 wheel, proved on this Mac's built-in devices and on a Windows or Linux PC, benchmarked against the Python bus. Justin decides Rust or Python from the numbers.
- It lives in this repo under native/relay-bus, built locally with maturin. CI wheels and cargo-deny for it come only if the spike is chosen.
- Issue 599 (always-on bus, destinations) waits for that decision so it is built once on the chosen core.
- The design follows Splitwave (MIT: per-consumer rings, command and discard queues, ring-fill drift loop) and CamillaDSP (MPL option: PI rate controller with rubato's async resampler). The wheel loads into the Python sidecar, not the Tauri process, because speech capture, AES67, NDI and speech injection all live in Python.

## Tried and ruled out
- Swapping the Rust core in behind a setting, or replacing the Python mixer outright: too much before knowing the numbers.
- CI builds for three OSes now, or a separate repository: not needed for a spike.
- Building 599's first PR on the Python bus in parallel: it would be built twice.
- No existing engine replaces the whole bus (GStreamer, miniaudio, JUCE, PipeWire, JACK, pyo and Rust game-audio crates each miss per-output clock following or Python feeds and taps).

## Watch out
- Only built-in Mac devices and a Windows or Linux PC are available; no multichannel interface or Dante gear, so drift between truly independent clocks is only simulated.
- Never copy GPL code (Inferno, Helvum, sap-rs are GPL/AGPL); from CamillaDSP copy only files whose header offers MPL-2.0.

In the code: native/relay-bus (workspace: relay-bus engine, later relay-bus-py), app/audiobus.py (the Python bus it is measured against), crates rtrb, rubato, cpal, pyo3, assert_no_alloc.
