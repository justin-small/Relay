---
title: "The Rust NDI module runs only when RELAY_RUST_NDI is set, until the whole module is ported"
author: Justin
date: 2026-11-24
branch: ndi-rust-module-658
cost: { minutes: 9, tokens: 265760 }
id: l-663f5e07
scope: repo
status: active
src: agent
touches:
  - native/modules/ndi/src/settings.rs
  - native/relay-server/src/modules.rs
---

## Decided
Issue 658 is ported in slices. Until all of it is in Rust, the Rust NDI module mounts no routes and does not start unless RELAY_RUST_NDI=1. Without it the Python NDI module keeps answering /api/admin/ndi and owns the NDI runtime. I chose this myself; Justin has not been asked.

## Tried and ruled out
Registering it unconditionally like signage: an install that has run Python already has modules.ndi.enabled, so the Rust server would serve a half-ported module in place of Python's, and two programs would load the NDI runtime.

## Watch out
- The loopback test (native/modules/ndi/tests/loopback.rs) skips when the NDI runtime is missing, as on this Mac, so the frame path to a JPEG has never run on real NDI.
- Adding a dependency to a module crate fails under --locked until `cargo update --offline -p relay-module-ndi` refreshes Cargo.lock.

In the code: native/modules/ndi/src/lib.rs (SWITCH), native/relay-server/src/modules.rs.
