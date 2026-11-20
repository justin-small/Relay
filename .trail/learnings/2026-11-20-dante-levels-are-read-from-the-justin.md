---
title: "Dante levels are read from the multicast heartbeat, not asked for over the control port"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 5, tokens: 65429 }
id: l-4482b41b
scope: repo
status: active
src: agent
---

## Decided
Dante levels come from the multicast heartbeat (224.0.0.233 port 8708, record 0x8002 signal presence, 0x8001 clock error in ppb), not from an ARC request. Issue 581.

## Tried and ruled out
Asking a device for levels with ARC 0x3400: Dante-BabelBox shows it is a channel listing. Clock leader, lock and errors (0x1200) are still a guess; the heartbeat only has the frequency error.

## Watch out
Parsed only against Relay own packet builder, never a real capture. Level byte: 0 clipping, 1-252 is -(byte/2) dBFS, 253+ silence. Link-local multicast is not IGMP-snooped, so it should reach any port; unchecked on the church network.

In the code: app/modules/dante/heartbeat.py, service.py read_levels, tests/test_dante_heartbeat.py.
