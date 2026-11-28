---
title: "Issue 674 ports Dante to Rust with devices kept as JSON, listeners as tasks, and small core capabilities for this computer's ports, alert cancel and the caller…"
author: Justin
date: 2026-11-28
branch: main
cost: { minutes: 1, tokens: 2903 }
id: l-2440aec5
scope: repo
status: active
src: agent
touches:
  - native/relay-core/src/caps.rs
  - docs/rust-server.md
---

## Decided
Issue 674: the Dante module is a crate (`native/modules/dante`) that keeps each device as a JSON object, so the admin page reads what it read from Python. Its tables are the Python migrations, applied by the module under the namespace `dante`; nothing is in config.json.
Core gained `core.interfaces` (this computer's ports and MACs), `AlertButtons::cancel`, and `Authorizer::who` / `Host::who` (the signed-in name for "by <who>" in the history). The module offers `gear.dante`, `audio.input_labels`, `dante.routing` and `nosound.dante` by name; nothing in the Rust server reads them yet.
The heartbeat and meter listeners are tasks that a stop aborts, so a stop no longer waits on a thread's socket timeout (the Python learning about keeping the join off the event loop).

## Tried and ruled out
Mocks: the fakes are real UDP sockets (a Dante device taking both write formats, a heartbeat sender, a meter-pushing box, an mDNS responder), so a whole scan runs.

## Watch out
Providing in `attach` would leave the offers up while the module is off; provide in `start` and `withdraw_all` in `stop`.
A heartbeat socket bound to every address cannot be sent to at 0.0.0.0 on macOS: send to 127.0.0.1 and the socket's port.
The v2 meter capture in the test must be copied from the Python test by a script, not retyped (one dropped byte makes the parse return nothing).
Health detail is a `&'static str`, so the counts and the first problem's text are not in it.
In the code: native/modules/dante, native/relay-core/src/caps.rs, docs/rust-server.md "Dante (#674)".
