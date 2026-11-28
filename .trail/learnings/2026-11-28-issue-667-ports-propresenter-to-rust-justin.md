---
title: "Issue 667 ports ProPresenter to Rust whole, with Planning Center reached through a plan link capability and the helper hub left to core"
author: Justin
date: 2026-11-28
branch: rust-propresenter-667
cost: { minutes: 54, tokens: 1309064 }
id: l-72f915b6
scope: repo
status: active
src: agent
touches:
  - native/modules/propresenter/src/lib.rs
  - native/modules/propresenter/tests/admin_page.rs
  - native/modules/propresenter/tests/module.rs
  - native/relay-core/src/caps.rs
  - native/relay-server/src/app.rs
  - native/relay-server/src/captions.rs
  - native/relay-server/src/lib.rs
  - native/relay-server/src/show.rs
  - native/relay-server/src/tagacts.rs
  - native/Cargo.lock
---

## Decided
Justin, 2026-11-28, issue 667: the whole module in one pull request (he chose that over slices), committed in slices on branch rust-propresenter-667 stacked on #666.
ProPresenter reaches Planning Center only through `plan.link` (the followed plans with items and LIVE, and `drive`); Planning Center's `drive` lives in its own crate and never takes control. The tag layer is `signage.layer.tag`, consulted by signage's `Store::resolve` above an override; tags drive screens through a new `signage.control` capability.
Songs: the descriptor set and the template slide are compiled in with include_bytes!, message types come from prost-reflect at run time (as Python builds them from pro7.desc).

## Tried and ruled out
lopdf 0.34: every version up to 0.41 has RUSTSEC-2026-0187 (stack overflow from nested objects in an uploaded PDF), and cargo deny fails on it. 0.47 with default features off is clean.
Testing the lobby and the link by poking the follower's fields, as the Python tests do: Rust tests cannot reach private state, so the fake ProPresenter models the announcements layer and the church's playlists and pushes the change, and the test waits for the follower to see it.
Porting the helper hub (/ws/helper pairing) here: it is core, not this module; the module asks for `core.helpers` and answers as Python does with no helper paired.

## Watch out
A std Mutex taken twice in one expression deadlocks a single-thread test (`(fake.lock().a, fake.lock().b)`); take one guard.
Cargo: this Mac's rsproxy mirror answers 504 now and then; run `cargo fetch` again before concluding a crate is missing.
Rust and Python ProPresenter both follow when both servers have the module on, as with Planning Center in #666.
In the code: native/modules/propresenter, native/relay-server/src/tagacts.rs, native/modules/planning_center/src/control.rs (drive), docs/rust-server.md "ProPresenter (#667)".
