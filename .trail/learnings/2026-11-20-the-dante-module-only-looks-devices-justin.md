---
title: "The Dante module only looks: devices by Dante name, and a control-port check whose packet is a guess"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 8, tokens: 85713 }
id: l-c86a7d69
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/__init__.py
  - app/modules/dante/routes.py
  - app/modules/dante/templates/dante.html
  - app/modules/dante/static/dante.js
---

## Decided
Issue 257: the dante module is read-only. It folds core's mDNS answers into one record per Dante device keyed by its Dante name (a computer on two ports is one device), takes every port from SRV, reads transmit channels from the `<channel>@<device>` instances, and offers them through gear.dante. Control (rename, subscriptions) is held until a packet capture or real gear exists.

## Tried and ruled out
- python-zeroconf and netaudio: core already scans, and netaudio's protocol lives in a native binary with no Python to copy.

## Watch out
The reachability check sends a 10-byte ARC header (0x27ff, length 10, sequence, command 0x1000) written from memory and never run on a real device; "no answer" may only mean the packet is wrong. The page says so. Nothing has run against real Dante gear.

In the code: app/modules/dante/service.py (probe_packet, inventory), tests/test_dante.py (real loopback UDP responder).
