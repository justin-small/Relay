---
title: "The Rust hub, settings and streams are built and tested but not mounted, because the caption engine still publishes into the Python hub"
author: Justin
date: 2026-11-23
branch: issue-656
id: l-3f3c250c
scope: repo
status: active
src: agent
touches:
  - docs/rust-server.md
  - native/Cargo.lock
  - native/Cargo.toml
  - native/relay-core/Cargo.toml
  - native/relay-core/src/config.rs
  - native/relay-core/src/lib.rs
  - native/relay-core/tests/core.rs
  - native/relay-server/Cargo.toml
  - native/relay-server/src/accounts.rs
  - native/relay-server/src/lib.rs
---

## Decided
Justin, 2026-11-23, issue 656: config and the key store live in a new relay-config crate, the Rust secretbox is byte-compatible both ways with Python, and this issue ships the hub plus the generic event-stream and websocket plumbing with /stream and the status streams. Alerts, tally, lyrics, helper and intercom streams come with their own issues through Streams::provide and ws::serve. The streams are not mounted on the live server: until captions move (#659) Python's hub holds the lines, so the front door keeps forwarding /stream. The blocklist is a Redact trait with a pass-through default until app/redact.py is ported.

## Tried and ruled out
Running tools/stress.py sse against the Rust routes: it starts the Python app. The 500-viewer check is a Rust test on real sockets instead.

## Watch out
Blocks another Python module cleans (audio_bus, schedules, boards, panels, alerts, network, ai) are kept as stored by relay-config until that module is ported; do not drop them in normalise. relay_core::Config::update now starts from the file on disk so it cannot undo a Settings save.
In the code: native/relay-config, native/relay-core/src/hub.rs, native/relay-server/src/streams.rs, docs/rust-server.md.
