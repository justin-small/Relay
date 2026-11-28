---
title: "Issue 668 ports OBS to Rust directly on obs-websocket, with new core capabilities and the scene screen at /obs/screen"
author: Justin
date: 2026-11-28
branch: main
cost: { minutes: 12, tokens: 661816 }
id: l-b09ed53d
scope: repo
status: active
src: agent
touches:
  - native/modules/obs/src/routes.rs
  - tests/test_rust_modules.py
  - native/relay-core/src/caps.rs
  - native/relay-server/src/modcaps.rs
  - docs/rust-server.md
---

## Decided
Issue 668: OBS is written directly against obs-websocket 5 over a routed TCP stream (tokio-tungstenite), not with obsr or obws. Core gained small capabilities for what OBS reports to and reads from it: stream status, alert buttons, the caption hub, a tally shape, Host::record for an audit entry with no request, and relay_core::access::Peer.
The scene screen moved to /obs/screen, because the Rust server does not own /screen.

## Tried and ruled out
Testing the module with mocks: the fake is a real WebSocket server, so the handshake, events and a dropped connection run for real.

## Watch out
tests/test_rust_modules.py flags `tokio::net` even as a type; mark the line `// allow-socket: <reason>`.
Cleaning settings is not idempotent for a name cut to 24 characters (it can end in a space), as in Python.

In the code: native/modules/obs, native/relay-core/src/caps.rs, native/relay-server/src/modcaps.rs, docs/rust-server.md "OBS Studio (#668)".
