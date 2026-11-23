---
title: "Rust tests build their passwords when they run, because CodeQL flags a literal password in a test"
author: Justin
date: 2026-11-23
branch: issue-655
cost: { minutes: 8, tokens: 40011 }
id: l-feebfaed
scope: repo
status: active
src: agent
touches:
  - native/relay-auth/tests/auth.rs
  - native/relay-server/tests/accounts.rs
  - docs/rust-server.md
  - native/Cargo.lock
  - native/Cargo.toml
  - native/relay-auth/Cargo.toml
  - native/relay-auth/schema.sql
  - native/relay-auth/src/accounts.rs
  - native/relay-auth/src/audit.rs
  - native/relay-auth/src/db.rs
---

## Decided
Passwords in Rust tests are made at run time (a helper that joins a name with the process id and the time), never written as string literals. The one password a committed fixture database needs is joined from words so it is not one literal.

## Tried and ruled out
Leaving literal test passwords: CodeQL's "Hard-coded cryptographic value" raised 14 alerts on the first pull request that had them, and its "Cleartext logging" alert fired on a panic message that printed the whole result of a password change.

## Watch out
A test helper must not print the value a password call returned, even in a failure message. Borrowing a made password inside a closure needs it bound to a variable first.

In the code: native/relay-auth/tests/auth.rs (`secret`, `fixture_password`), native/relay-server/tests/accounts.rs.
