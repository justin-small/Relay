---
title: "Architecture lint (deslop-please) cycles are broken by a lower layer; field and shallow findings that need an API redesign were left, not hidden behind getters"
author: Justin
date: 2026-11-28
branch: deslop-native
cost: { minutes: 9, tokens: 87329 }
id: l-9fcc3a95
scope: repo
status: active
src: agent
---

## Decided
Ran deslop-please 0.1.6 on native/ (needs rust-analyzer; about 50 s). The three module cycles (relay_auth, relay_bus, relay_core) were broken by moving shared pieces into a module that uses none of the others, not by allow-listing. Single-owner field leaks got narrow methods (recv, try_recv, set) instead of public fields.

## Tried and ruled out
Getters or Deref to silence the Accounts::auth leak (49 places in relay-server): that hides the problem. The real fix is turning the route handlers into methods or splitting them by area, which was not done. Splitting Auth into sub-handles to cure the relay_auth shallow-module finding: it changes every caller, so it was left.

## Watch out
The linter counts every setter as a leak (Host::set_viewer_port, Server::set_tls), and new modules add to the wide-directory count (relay-core/src is at 19 files, limit 15). relay-bus memory test heap_stays_flat times out on main too.

In the code: relay-auth/src/shared.rs, relay-bus/crates/relay-bus/src/discard.rs, relay-core/src/future.rs and gate.rs; run `deslop-please` from native/.
