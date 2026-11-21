---
title: "NDI runs in Rust: dlopen the runtime, one receiver thread per source, and the Python binding's slow receive is gone"
author: Justin
date: 2026-11-21
branch: rust-ndi-606
cost: { minutes: 22, tokens: 444855 }
id: l-dcb01c20
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/testpatterns.py
  - native/relay-ndi/crates/relay-ndi-py/python/relay_ndi/__init__.py
  - native/relay-ndi/crates/relay-ndi-py/src/lib.rs
  - native/relay-ndi/crates/relay-ndi/src/lib.rs
  - native/relay-ndi/crates/relay-ndi/src/patterns.rs
  - tests/test_ndi_testpatterns.py
  - app/modules/ndi/viewer.py
  - native/relay-ndi/Cargo.lock
  - native/relay-ndi/crates/relay-ndi/Cargo.toml
  - native/relay-ndi/crates/relay-ndi/src/picture.rs
---

## Decided
Justin, 2026-11-21, issue 606: move all of the NDI module's work to Rust, receive included, with a real NDI source to prove it. `native/relay-ndi` (wheel `relay_ndi`) now holds the finder, receiver, sender, router, picture scopes, audio meter, viewer JPEG encode and test patterns; cyndilib is out of `requirements` and runtime.py. Python keeps routes, settings, templates, mDNS and BirdDog discovery, router/output bookkeeping, recording CSVs, Pillow text, PNG reading and the Playwright page renderer: it is not NDI work and not hot.
The NDI runtime is opened with dlopen (the structs are hand-declared from the SDK 6 headers), not linked, so a build needs neither the SDK nor its headers and Relay still ships none of NDI's libraries. `NDI_RUNTIME_DIR_V6` wins; the `cyndilib` package's copy is a fallback only if that package happens to be installed.
In the code: `native/relay-ndi/crates/relay-ndi/src/{ffi,recv,scopes,audio,picture,patterns}.rs`, `app/modules/ndi/runtime.py`, `tools/build_bus_wheel.py` (builds both wheels).

## Tried and ruled out
The grafton-ndi crate: it links the NDI SDK at build time (bindgen and headers), which every CI runner and release build would then need, and the licence forbids bundling.
Rust scopes alone: the old receiver delivered 13 of 30 fps, so the verdicts would still be wrong; receive had to move too. A 1080p30 UYVY loopback now reads 30.1 fps, 0 late frames, health Steady.
Text in Rust: the burned-in name and clock stay Pillow patches blended in by Rust; only the moving pattern's frame counter uses a built-in 5x7 font.

## Watch out
Supersedes the cyndilib-specific findings in older NDI learnings (frames must be writable, routing through ctypes, get_all_read_data, sending and receiving in one Python process starving both): none applies to the Rust engine, which captures on its own thread and takes any bytes.
The NDI Test Patterns app sends one frame for a still pattern, so it is no benchmark; the proof is `native/relay-ndi/python/proof.py` (a second process sends a moving 1080p30 picture). Without cyndilib there is no bundled runtime: an operator needs NDI Tools or the NDI runtime installed, and on a Mac without it `NDI_RUNTIME_DIR_V6` must point at a folder holding libndi.dylib.
The Rust receiver queues eight pictures; a slower reader loses the oldest and `queue_overflow` in `perf()` counts them.
