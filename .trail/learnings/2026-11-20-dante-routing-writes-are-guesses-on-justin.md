---
title: "Dante routing writes are guesses on the wire, so every write is confirmed, read back and undoable, and locks beat presets"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 6, tokens: 141778 }
id: l-bc501a41
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/routing.py
---

## Decided
Issues 259 and 260 built together in the dante module: subscribe/clear needs dante.control and a `confirm: true` body, each write is read back and reported as ok, refused, silent or unconfirmed, the last change is kept in memory for one-level Undo (a preset apply undoes as one), locked receive channels (dante_locks, dante.manage) are skipped by both single changes and presets and listed, and a preset stores every readable receive channel including empty ones so applying restores removed and added routes.

## Tried and ruled out
Persisting Undo across restarts: kept in memory, since the routing it would restore may no longer match the network.

## Watch out
The set-subscription packet (0x3010) in app/modules/dante/arc.py is Relay's assumption like the other layouts; the fake device in tests/test_dante_routing.py speaks the same assumption. Nothing was tried on real Dante gear, so the page says "untested". Dante Domain Manager refusals are shown as the device's refusal; DDM credentials are not supported.

In the code: app/modules/dante/routing.py, routes.py, migrations/0002_routing.sql, tests/test_dante_routing.py.
