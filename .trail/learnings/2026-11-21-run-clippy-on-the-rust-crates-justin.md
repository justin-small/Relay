---
title: "Run clippy on the Rust crates before pushing, because the full test runner does not"
author: Justin
date: 2026-11-21
branch: ndi-audio-sync-alerts-bridge
cost: { minutes: 8, tokens: 476815 }
id: l-364fe84a
scope: repo
status: active
src: agent
touches:
  - native/relay-ndi/crates/relay-ndi/src/sink.rs
  - app/modules/ndi/bridge.py
---

## Decided
Before pushing any change under native/, run clippy with warnings as errors in each crate you touched, as well as the full suite.
## Watch out
The full Python suite builds the Rust wheels and runs their tests but never runs clippy, so PR 619 passed locally and failed CI on three lints (repeat().take(), map_or). Newer clippy also wants repeat_n and is_none_or.
In the code: `cd native/relay-ndi && cargo clippy --all-targets -- -D warnings` (same in native/relay-bus).
