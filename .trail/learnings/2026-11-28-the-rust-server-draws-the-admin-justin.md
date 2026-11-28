---
title: "The Rust server draws the admin panel and learns the app's permissions from the startup plan"
author: Justin
date: 2026-11-28
branch: admin-pages-in-rust-module-trait
cost: { minutes: 22, tokens: 281917 }
id: l-fa061690
scope: repo
status: active
src: agent
touches:
  - app/front.py
  - app/modules/__init__.py
  - docs/rust-server.md
  - native/modules/ndi/src/lib.rs
  - native/modules/ndi/tests/admin_page.rs
  - native/modules/remote_access/src/lib.rs
  - native/modules/remote_access/tests/admin_page.rs
  - native/modules/signage/src/lib.rs
  - native/modules/signage/tests/admin_page.rs
  - native/modules/signage_web/src/lib.rs
---

## Decided
Justin, 2026-11-28, issue 719: the Rust server serves /admin itself. Modules it has declare pages in the module trait; modules only the Python app has are listed from the app on each load and merged, and the app's permissions are registered in the Rust role table from the startup plan.

## Tried and ruled out
Checking the panel only with unit tests: they passed while a Python-only module (OBS) was missing from the live panel, because the Rust role table did not know its permissions. Only running the Rust server in front of the app showed it. Naming real module crates in relay-server tests: tests/test_rust_modules.py refuses it, so the server's tests use stub modules and each module crate compares itself with the recorded Python render.

## Watch out
A permission of a module the Rust server does not have is unknown to `can` and to /api/admin/me until the plan registers it. The browser test only drives the Python app, so it does not cover the Rust panel. The Python admin_pages declarations stay until #664.

In the code: native/relay-server/src/accounts/page.rs, native/relay-server/src/app.rs (plan permissions), app/front.py, native/modules/<id>/tests/admin_page.rs.
