---
title: "A cable is one core record between two ports; a module's reported cable can be annotated but not removed"
author: Justin
date: 2026-11-19
branch: cables-541
cost: { minutes: 9, tokens: 210804 }
id: l-56db47c3
scope: repo
status: active
src: agent
---

## Decided
Issue 541 (2026-11-19), Justin's answers:
- Blackmagic's saved cables move to core cables once, then the `cables` key leaves its config; its routes are thin wrappers over core.
- UniFi's uplink cables are synced each refresh. One no longer reported is marked missing, not dropped. An operator can annotate it, never delete it.
- A port may have many cables (patch panels, splits). Only the same two ports twice is refused, whichever way round.
- The Network page draws every cable, any port type. The Blackmagic Wiring tab shows those with both ends on Blackmagic devices.
- End `a` is the one entered first (an output); uniqueness is on the unordered pair.

## Tried and ruled out
- One cable per port, with the reported cable winning: breaks patch and split cases.
- Dropping a reported cable when UniFi stops reporting it: loses its note.

## Watch out
- A reported cable's device end is the device's flagged uplink port, else a port `eth` made on it. Wireless clients have no switch port, so no cable.
- `cables.sync` only marks missing the sources that were live and not stale this read; an off module keeps its cables untouched.

In the code: app/cables.py, app/migrations/0013_cables.sql, network_map.sync_cables and cable_rows, Blackmagic service.cables/_move_cables, tests/test_cables.py.
