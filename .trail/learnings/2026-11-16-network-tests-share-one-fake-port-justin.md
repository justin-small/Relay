---
title: "Network tests share one fake-port fixture, Relay joins only a Dante device's primary network, and the multi-port hardware check is a manual plan before release"
author: Justin
date: 2026-11-16
branch: network-guide
cost: { minutes: 8, tokens: 80784 }
id: l-1bcf3965
scope: repo
status: active
src: agent
touches:
  - app/modules/alert_events/__init__.py
  - app/modules/alert_events/events.py
  - app/modules/atem/__init__.py
  - app/modules/atem/client.py
  - app/modules/crew/__init__.py
  - app/modules/crew/webpush.py
  - app/modules/intercom/__init__.py
  - app/modules/intercom/livekit.py
  - app/modules/planning_center/__init__.py
  - app/modules/planning_center/planningcenter.py
---

## Decided
Issue 349 (2026-11-16), built without asking Justin: the operator chapter is "Using more than one network port" in the user guide, after Remote access. It describes Dante and NDI as arriving later, since those modules do not exist yet. Relay joins one Dante network, the primary; a Dante module declares no class for the secondary. Trademark lines: "works with Dante devices" (Audinate) and "NDI is a registered trademark of Vizrt NDI AB".
The manual hardware test plan (two USB adapters on a Mac and Windows, pull/replug, sleep/wake, panel unreachable on the Dante port) lives with the reference docs, not in CI.
The contract test now also refuses a traffic class declared by two modules (or by a module and core); before, the second was silently dropped.

## Watch out
Fake ports come from tests/_netfake.py's port(name, ipv4, friendly, mac=...): the id is built from `mac`, so two ports with the same mac are the same port to Relay. Tests that need other friendly names (test_netroute's "USB 10/100/1000 LAN") make their own with port() instead of changing the shared ones, whose names other tests assert on.

In the code: tests/_netfake.py, docs/guide/src/36-network-ports.md, docs/reference/multi-nic-hardware-test.md, docs/modules.md "Network traffic", traffic checks in tests/test_modules.py.
