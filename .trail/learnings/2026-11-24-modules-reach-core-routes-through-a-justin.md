---
title: Modules reach core routes through a relay-core capability that relay-audio implements
author: Justin
date: 2026-11-24
branch: racks-page-mockup
cost: { minutes: 12, tokens: 239247 }
id: l-689935b9
scope: repo
status: active
src: agent
touches:
  - docs/mockups/racks/index.html
  - docs/mockups/racks/racks.css
---

## Decided
Justin, 2026-11-24, issue 658: the Rust NDI router reaches channels, routes and presets through a trait in relay-core (`Routes`, plus `RouteProvider` for a module's own destinations), which relay-server implements on relay-audio and offers through Host. Every later module that owns a destination (#661, #662) uses the same door. A ported module's provider is kept in its own slot in the routing, so the app's periodic refresh does not wipe it.

## Tried and ruled out
Keeping core routes in Python for now (offered, not chosen). A module cannot depend on relay-audio, and a test in relay-server cannot name a module crate: the module-rule test fails on any relay-server source outside modules.rs, tests included. So the real routing is tested in relay-server with a fake provider, and the router logic in the module crate with in-memory routes.

## Watch out
- The capability is only provided when the server runs with --rust-audio; otherwise the NDI router logs that routing is unavailable.
- Routes saved before #544 are not migrated by Rust; Python's migrate still does that.

In the code: native/relay-core/src/routes.rs, native/relay-server/src/routescap.rs, native/modules/ndi/src/router.rs, native/relay-server/tests/routescap.rs.
