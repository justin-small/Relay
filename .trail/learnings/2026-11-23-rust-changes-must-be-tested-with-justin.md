---
title: "Rust changes must be tested with nextest, quickcheck properties and cargo-mutants, and the pull request says which were run"
author: Justin
date: 2026-11-23
branch: rust-listeners-653
cost: { minutes: 1, tokens: 6912 }
id: l-8dd14e4c
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - native/.config/nextest.toml
  - native/.cargo/mutants.toml
  - native/relay-server/tests/properties.rs
---

## Decided
Justin, 2026-11-23, PR 690: the Rust testing tools from the helper work (nextest, quickcheck properties, cargo-mutants) apply to every change under native/, not only the helper. CI still runs only cargo test and clippy, so the rest is by hand and the PR names what was run.

## Tried and ruled out
Copying the helper's clippy.toml and rustfmt.toml to the workspace: its no-bool-parameters rule would fail existing crates.

## Watch out
A surviving mutant means a missing test; add it or explain why it is harmless.
In the code: AGENTS.md ("Rust tests"), native/.config/nextest.toml, native/.cargo/mutants.toml, native/relay-server/tests/properties.rs.
