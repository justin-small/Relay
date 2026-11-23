---
title: "The Rust server is one native workspace with one crate per module, all compiled in and switched at run time"
author: Justin
date: 2026-11-23
branch: rust-server-design-652
id: l-93f41357
scope: repo
status: active
src: agent
touches:
  - docs/rust-server.md
  - native/Cargo.toml
  - tests/test_rust_modules.py
  - ".github/workflows/tests.yml"
---

## Decided
Justin, 2026-11-23, issue 652, his answers: every integration in the Rust server is its own crate (relay-module-<id>), compiled in and switched on and off at run time like the Python modules; only the server crate may depend on a module crate. All Rust under native/ (bus, ndi, rf, helper, front, server) moved into one Cargo workspace, in this issue, not later. Errors are a code enum with a fixed sentence and status, body {"detail": sentence}, the cause only in the log. config.json and relay.db are read in place so a Python install upgrades with no migration.

## Tried and ruled out
Cargo features in one crate (the boundary would only be a convention) and run-time plugins (too much work). Keeping the helper and RF crates as their own workspaces: not chosen; the helper's size-optimised release became the release-helper profile instead.

## Watch out
Required CI check names changed: the bus, NDI and helper jobs are one "Tests (Rust workspace)" job. relay-rf's hardware test failed under WSL because there is no USB bus; it now skips. Windows and Linux x86 runs go through desktop-3f1897k (relay-win, WSL Ubuntu).

In the code: docs/rust-server.md, native/Cargo.toml, native/relay-core, tests/test_rust_modules.py, .github/workflows/tests.yml.
