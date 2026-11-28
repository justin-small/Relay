---
title: "Signage web checks connect to the address they just validated, and modules look names up through core"
author: Justin
date: 2026-11-28
branch: main
cost: { minutes: 6, tokens: 199087 }
id: l-7faacaee
scope: repo
status: active
src: agent
touches:
  - native/relay-core/src/net.rs
  - native/modules/signage_web/src/links.rs
  - native/modules/signage_web/src/check.rs
  - native/modules/signage_web/src/lib.rs
  - tests/test_rust_modules.py
  - app/modules/signage_web/links.py
  - app/modules/signage_web/routes.py
  - app/modules/signage_web/sourcing.py
---

## Decided
Issue 660: signage_web is a Rust module crate. Each check hop resolves the host once through core, refuses it unless every address is public, then connects to that checked IPv4 address (Host header and TLS name stay the hostname), so a second lookup cannot swap in a private address.

## Tried and ruled out
Using tokio::net::lookup_host in the module: tests/test_rust_modules.py bans tokio::net in modules. The lookup lives in core as relay_core::net::resolve_all.

## Watch out
Health detail is a &'static str, so the Python "N saved links" count cannot be shown. The Rust module trait has no admin pages, so the Web & YouTube tab stays in Python. cargo mutants was not run on the new crate.
In the code: native/modules/signage_web/src/check.rs, native/relay-core/src/net.rs.
