---
title: "The Rust server reads relay.db without replaying migrations and owns sign-in, tokens, devices and the audit log"
author: Justin
date: 2026-11-23
branch: issue-655
cost: { minutes: 14, tokens: 315663 }
id: l-41618d61
scope: repo
status: active
src: agent
touches:
  - native/relay-auth/schema.sql
  - native/relay-server/src/accounts.rs
  - tests/test_rust_modules.py
  - docs/rust-server.md
  - app/users.py
---

## Decided
Justin, 2026-11-23, issue 655, his answers: the Rust server does not replay the 16 Python migrations. A new relay.db gets native/relay-auth/schema.sql (the auth tables after migrations 0001-0004, 0009, 0010) with those six versions recorded; an existing file must already hold them or is refused naming the missing one. Rust owns the sign-in forms and the account, token, device and audit routes and writes their audit entries; sqlx is the database crate; new hashes are argon2id while Python's scrypt hashes still verify and are rewritten on sign-in.

## Tried and ruled out
sqlx 0.8: its bundled SQLite (libsqlite3-sys 0.30) has RUSTSEC-2026-0459 and fails cargo-deny; sqlx 0.9 allows libsqlite3-sys 0.40. Auditing forwarded Python routes from the Rust gate: Python's routes write their own notes, so it would double the entries. Porting /admin/setup now: it stores the OpenAI key as a sealed secret, which belongs to #656.

## Watch out
Argon2id hashes cannot be read by Python, so rolling back to the Python server asks those people for a new password. sqlx 0.9 needs sqlx::AssertSqlSafe around any built statement; every one in relay-auth is constants only. tests/test_rust_modules.py needs Python 3.11+ (tomllib); the Mac's python3 is 3.10, use /opt/homebrew/bin/python3.15.

In the code: native/relay-auth (schema.sql, src/), native/relay-server/src/accounts.rs, tests/auth.rs, tests/accounts.rs, docs/rust-server.md.
