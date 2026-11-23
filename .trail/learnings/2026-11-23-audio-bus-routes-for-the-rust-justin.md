---
title: "Audio bus routes for the Rust server live in relay-server, with the matrix cached in memory and written through to the database"
author: Justin
date: 2026-11-23
branch: issue-657
id: l-163a9c34
scope: repo
status: active
src: agent
touches:
  - app/audiobus.py
  - app/routing.py
  - app/channels.py
  - app/metering.py
---

## Decided
Justin, 2026-11-23, issue 657: the ported audio bus, routing, channels and meter admin routes go into the relay-server crate as axum handlers, because the bus is core, not an integration. The crosspoint matrix is held in memory in the bus and written through to SQLite on every change, which also settles #648. Tests for the routes are new Rust tests; the Python TestClient tests wait for #663.

## Tried and ruled out
- A separate crate mounted by relay-server: not chosen, the bus is core.
- Reading SQLite on every request with a cache on top: not chosen, memory plus write-through is simpler to keep fresh.
- Keeping the Python tests running through the strangler forward: not chosen.

## Watch out
The engine (native/relay-bus) was already Rust; only the layer above it was Python, so the port is mostly routes, routing and channel storage, not DSP.

In the code: native/relay-server, native/relay-bus/crates/relay-bus, native/relay-bus/crates/relay-bus-cpal (reused for devices), app/audiobus.py, app/routing.py, app/channels.py, app/metering.py.
