---
title: "Modules hear each other through Host events, and core catalogs are refreshed before they are read"
author: Justin
date: 2026-11-28
branch: rust-planning-center-666
cost: { minutes: 6, tokens: 157729 }
id: l-762c5fbb
scope: repo
status: active
src: agent
touches:
  - native/relay-server/src/modcaps.rs
  - native/relay-core/src/module.rs
  - native/relay-core/src/caps.rs
  - native/modules/planning_center/src/lib.rs
---

## Decided
Issue 666 follow-up: Rust modules talk by name-keyed events on `Host` (`listen`, `announce`, `withdraw_all`), the Rust form of Python's `modules.listen` / `announce`. The directory (teams and positions) and the audio channels are offered as capabilities whose methods are plain functions over a copy; the caller awaits `refresh()` once before reading. This keeps the rundown code synchronous while the tables are read with sqlx.
A module registers its listeners in `start()`, not `attach()`, because `stop()` calls `withdraw_all`, which also removes listeners; `attach` runs only once at startup.
The panels' `service` list comes from the module's `PlanCap.offered` while Planning Center is on, and `plan.offered_changed` pushes the status.

## Tried and ruled out
Making the capability traits async: the rundown code calls them from many synchronous places.
Registering the `directory.changed` listener in `attach`: it is lost after the first switch off and on.

## Watch out
`directory.changed` is announced from inside `DirectoryStore::sync`; a listener that calls `refresh` again is safe only because an unchanged sync announces nothing.
In the code: native/relay-core/src/module.rs (Host events), native/relay-core/src/caps.rs (event names, `refresh`), native/relay-server/src/modcaps.rs, native/modules/planning_center/src/lib.rs and routes.rs.
