---
title: "Tests that talk to the panel trust its own certificate through an explicit TLS 1.2 context, because CodeQL rejects both unverified and default contexts"
author: Justin
date: 2026-11-23
branch: rust-listeners-653
cost: { minutes: 10, tokens: 77333 }
id: l-760d8055
scope: repo
status: active
src: agent
touches:
  - native/.cargo/mutants.toml
  - native/.config/nextest.toml
  - native/Cargo.lock
  - native/relay-server/Cargo.toml
  - native/relay-server/tests/properties.rs
  - tests/test_rust_listeners.py
  - app/front.py
  - app/main.py
  - app/tls.py
  - docs/rust-server.md
---

## Decided
Justin, 2026-11-23, PR 690: a test that connects to the HTTPS panel builds an SSLContext with minimum TLS 1.2 and loads the panel's own certificate as the only trusted one, so verification stays on.

## Tried and ruled out
Turning verification off (CERT_NONE) and ssl.create_default_context(cafile=...): CodeQL's insecure-protocol rule flagged both, the second for allowing TLS 1.0 and 1.1.

## Watch out
cargo-mutants takes -f paths relative to the workspace root (relay-server/src/gate.rs), not the package; a package-relative path finds 0 mutants and exits 0. Run it from native/.
In the code: tests/test_rust_listeners.py (trusting_context), native/.cargo/mutants.toml.
