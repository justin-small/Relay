---
title: "Issue 662 ports intercom, remote intercom and remote access in one pull request, with a routed outbound connect built in core first"
author: Justin
date: 2026-11-23
branch: worktree-issue-662-intercom-rust
cost: { minutes: 4, tokens: 55990 }
id: l-83356bfa
scope: repo
status: active
src: agent
touches:
  - docs/rust-server.md
  - native/relay-core/src/module.rs
  - native/relay-server/src/netroute.rs
  - tests/test_rust_modules.py
---

## Decided
Justin, 2026-11-23, issue 662: port all three modules (remote_access, intercom, intercom_remote) in ONE pull request, in that order. Use the livekit Rust crates. Build a small routed-connect helper in core first, so modules keep the rule that traffic leaves by the network port the operator assigned. Each module gets its own schema.sql recorded in schema_migrations, like relay-auth. The device check (a phone through the Cloudflare tunnel) is driven from this Mac; it needs Justin's Cloudflare account and a real phone for the last step.

## Tried and ruled out
- Raw tokio::net in modules: the OS would pick the interface, so on a multi-port show machine intercom traffic could leave the wrong port, and no Rust test enforces the rule yet.
- One PR per module: offered, Justin chose a single PR.
- Replaying Python migrations at run time: more moving parts than a schema file.
- Assuming no Rust server existed: it does (relay-core, relay-server, Module trait); native/modules/ is just empty until the first module.

## Watch out
Check the repo before asking whether a dependency exists. The outbound netsock half is deferred in docs/rust-server.md to #661.

In the code: native/relay-core/src/module.rs, native/relay-server/src/netroute.rs, tests/test_rust_modules.py, docs/rust-server.md ("How integrations are modules is enforced").
