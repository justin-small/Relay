---
title: "Rust crates use BeautifulRust's strict clippy lints, minus the ones meant for published libraries"
author: Justin
date: 2026-11-23
branch: helper-releases-636
cost: { minutes: 1, tokens: 23385 }
id: l-ec2cf4a1
scope: repo
status: active
src: agent
touches:
  - native/relay-helper/Cargo.toml
---

## Decided
Justin asked for BeautifulRust's clippy lints. They are applied to relay-helper only, as a first crate. Relay's crates are applications, so the library-only lints are left out or allowed: missing docs, Errors/Panics doc sections, doc backticks, exhaustive enums, print to stdout/stderr. Everything else (unwrap, unsafe-needs-SAFETY, as-casts, allow-needs-reason, missing Debug, Rc clone style) stays on and was fixed in code.

## Tried and ruled out
The lints exactly as published: about 110 warnings on relay-helper, mostly doc lints, and CI runs clippy with -D warnings, so it would have failed every pull request.

## Watch out
`cargo clippy --fix` turns `#[allow(dead_code)]` into `#[expect(dead_code)]`. On autostart.rs the Windows-only constants keep it, but on a public fn it became an unfulfilled expectation on macOS and failed the build, so the attribute was removed there. relay-bus, relay-ndi and desktop/src-tauri do not have the lints yet; expect many more pedantic warnings in their FFI and audio code.

In the code:
- native/relay-helper/Cargo.toml: `[lints.rust]` and `[lints.clippy]` blocks
- native/relay-helper/clippy.toml: max-fn-params-bools = 0, allow unwrap/expect/print in tests
- check with: cargo clippy --locked --all-targets -- -D warnings
