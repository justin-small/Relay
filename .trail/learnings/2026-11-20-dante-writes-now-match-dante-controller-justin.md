---
title: "Dante writes now match Dante Controller's own packets byte for byte"
author: Justin
date: 2026-11-20
branch: dante-arc-real-packets
id: l-7950738c
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/arc.py
  - tests/test_dante_routing.py
---

## Decided
Writes follow what Dante Controller really sent (captured 2026-11-20 on the church network, a DT168 stage box): protocol 0x2729, subscribe is 0x3010 with a 0x08 byte and the strings from byte 172, and clearing is the same 0x3010 with both string pointers zero. The tests rebuild those captured packets byte for byte.

## Tried and ruled out
Clearing with 0x3014 (what network-audio-controller sends) and 0x02 with the strings at byte 52: neither matches what Controller does to this hardware, so neither is used. Rename (0x2013) and the 0x1200 clock are not built yet.

## Watch out
Still never sent to a real device; the first write should be a subscribe on a channel nobody uses, with read-back. Devices on ARC 2.8 (the Via PC, protocol 0x2809) use another format (issues 583 and 584). The capture is on basecenter at D:\dante.pcapng.

In the code: app/modules/dante/arc.py (subscribe_request), tests/test_dante_routing.py.
