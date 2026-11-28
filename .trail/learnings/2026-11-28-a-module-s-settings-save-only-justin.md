---
title: "A module's settings save only merges, so removing a key needs the new replace call"
author: Justin
date: 2026-11-28
branch: main
cost: { minutes: 21, tokens: 896802 }
id: l-eb1ced8c
scope: repo
status: active
src: agent
touches:
  - native/relay-core/src/caps.rs
  - native/modules/planning_center/src/plan.rs
  - native/modules/planning_center/src/client.rs
  - native/modules/planning_center/src/follower.rs
  - native/modules/planning_center/src/rundown.rs
  - native/modules/planning_center/src/control.rs
  - native/modules/planning_center/src/signagesync.rs
  - native/modules/planning_center/src/routes.rs
  - native/modules/planning_center/tests/planning_center.rs
  - native/modules/planning_center/tests/signage_sync.rs
---

## Decided
Issue 666: the settings store gets `replace`, which overwrites a module's whole settings object. Planning Center must drop keys (a plan pick that lapses, a disconnect, redraws that clear mic draws), and `save` merges objects key by key, so a removed key would come back.

## Tried and ruled out
Saving a smaller object with `save`: the old keys stay. Giving `replace` a default that calls `save`: it would silently merge, so every store implements it.

## Watch out
Any new `SettingsStore` implementation (tests included) must implement `replace`. The server's sealed store passes the settings key in the replace list of `relay_config::Settings::save`.

In the code: relay-core/src/store.rs, Host::replace_settings in relay-core/src/module.rs, SealedStore in relay-server/src/modulehost.rs.
