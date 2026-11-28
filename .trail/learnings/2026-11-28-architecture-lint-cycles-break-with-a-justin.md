---
title: "Architecture lint: cycles break with a lower layer, a crate stops being shallow by merging calls used together, and leaks need handlers inside the type"
author: Justin
date: 2026-11-28
branch: deslop-native
cost: { minutes: 5, tokens: 88673 }
id: l-59308300
scope: repo
status: active
replaces: l-9fcc3a95
src: agent
touches:
  - native/relay-server/src/accounts.rs
---

## Decided
deslop-please cycles are broken by moving shared pieces into a module that uses none of the others. Account route handlers became associated functions of the type that owns the database handle, in a folder split by area, with the fields private. relay_auth's shallow-module finding was cleared by merging calls always used together (audit_filters, pin_picker), not by splitting Auth into sub-handles.

## Tried and ruled out
Getters or Deref to silence a field leak: hides it. Splitting Auth into handle types: about 160 call-site edits, and the rule still fires because it also counts types shown. State<Shared> in an impl of the type itself creates a tangle with the alias; use State<Arc<Accounts>>.

## Watch out
The shallow-module rule fires when shown functions outnumber hidden ones (relay_auth is 47 shown against 49 hidden, so one new public method brings it back). Splitting a file with a crate-level #![allow] drops the allow: put it in the new mod.rs or clippy result_large_err fails. The relay-bus memory test times out on main too.

In the code: relay-server/src/accounts/ (mod, request, signin, me, admin, audit), relay-auth/src/shared.rs, Auth::audit_filters and Auth::pin_picker; run `deslop-please` from native/.
