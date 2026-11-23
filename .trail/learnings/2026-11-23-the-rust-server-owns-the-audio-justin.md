---
title: "The Rust server owns the audio engine when started with --rust-audio, and the Python modules reach it through a loopback door"
author: Justin
date: 2026-11-23
branch: issue-657
id: l-2653c44b
scope: repo
status: active
src: agent
touches:
  - app/audiobus.py
  - app/front.py
  - app/main.py
  - app/metering.py
  - docs/rust-server.md
  - native/Cargo.lock
  - native/Cargo.toml
  - native/relay-server/Cargo.toml
  - native/relay-server/src/accounts.rs
  - native/relay-server/src/app.rs
---

## Decided
Justin, 2026-11-23, issue 657: with `relay-server --rust-audio` the Rust server owns the sound cards, lanes, matrix and meters, and the Python app (started with RELAY_RUST_AUDIO=1) follows it. Python modules keep the bus API they use through app/audiobus_remote.py, which calls the server's loopback door (X-Relay-Front secret, loopback peer only). For gear still owned by a Python module (apply, read and rename on a route's destination, the audio sources and outputs it registered) the server calls back into the app under /api/internal/. The crosspoint matrix is cached in memory and the routes in relay.db stay the stored truth. Off by default: without the switch nothing changes.

## Tried and ruled out
- Rust routes only when Python is off, keeping the Python engine live: no p50 win until the consumers move.
- Python keeps the engine and Rust only caches its answer: not a port.
- Calling a Rust function per route from the PyO3 wheel: Justin wants Rust all the way down.
- A cache that only expired by time: a route written through Routing::replace reached the engine stale until Routing got its own generation counter in the cache key.

## Watch out
Two programs cannot open the same sound card, so the server and the app must be started with the same switch. The remote intercom links its mixer to audiobus.bus.engine in process and cannot work with the server owning the engine until #662. The server's setup-token sign-in compares x-admin-token to a config.json value Python seals, so tests sign in with a real session.

In the code: native/relay-audio, native/relay-server/src/{audio,internal,pyapp}.rs, app/audiobus_remote.py, app/front.py, tests/test_rust_audio.py.
