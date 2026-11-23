---
title: "Rust modules use tokio sockets with Relay's port routing wrapped around them, not a separate socket layer"
author: Justin
date: 2026-11-23
branch: worktree-issue-662-intercom-rust
cost: { minutes: 16, tokens: 233144 }
id: l-667650af
scope: repo
status: active
src: agent
touches:
  - native/Cargo.lock
  - native/modules/remote_access/Cargo.toml
  - native/modules/remote_access/src/cloud.rs
  - native/modules/remote_access/src/lib.rs
  - native/modules/remote_access/src/service.rs
  - native/modules/remote_access/src/tunnel.rs
  - native/modules/remote_access/tests/lifecycle.rs
  - native/relay-core/src/module.rs
  - native/relay-core/src/net.rs
  - native/relay-server/Cargo.toml
---

## Decided
Justin, 2026-11-23, issue 662: when I framed the choice as "routed helper vs raw tokio", he asked whether tokio could be used with the routing built around it. Yes: the sockets are tokio's. Relay resolves the traffic class to a network port, pins the socket to that interface with socket2 before handing it to tokio, and modules only ever ask Host::net(). Do not present tokio and routing as alternatives.

## Tried and ruled out
Raw tokio::net inside modules: the OS would pick the interface, and nothing enforces the rule. A bespoke non-tokio socket layer: not wanted.

## Watch out
Modules may depend only on relay-core, so the interface (Router trait, Net) lives in core and relay-server implements it. Windows pinning (IP_UNICAST_IF, SIO_UDP_CONNRESET) is untested here; CI on Windows is the check.

In the code: native/relay-core/src/net.rs, native/relay-server/src/netsock.rs, tests/test_rust_modules.py (SOCKET_USE).
