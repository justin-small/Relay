---
title: On the church network the control port answers across the router but the Dante heartbeat does not
author: Justin
date: 2026-11-21
branch: main
cost: { minutes: 3, tokens: 66743 }
id: l-2b9fd41e
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/arc.py
---

## Decided
2026-11-20, probed over Tailscale SSH from a Windows PC on both the Dante VLAN and the main LAN: read-only ARC requests (0x1000, 0x1002, 0x1003, 0x1100) are answered by DT168 stage boxes from the LAN across the router, and Via answers on 24440 (not 4440). So the 0x1000 probe packet is right. Liveness is "heartbeat seen recently OR control port answers"; the heartbeat is the extra signal, the control port stays the fallback.

## Tried and ruled out
Heartbeat as the only liveness signal: multicast 224.0.0.233:8708 was seen on the Dante interface from every device but nothing on the LAN interface (the router does not route it), so a Relay on the main LAN would see every device as down.

## Watch out
Dual-homed computers answer from their LAN address even when asked on the Dante address. 0x1000 payload: tx count u16 at byte 2, rx at 4. 0x1003: string pointers at 6 (board), 12 (name), 14 (hostname). 0x1100: (tag, pointer) pairs from byte 2; tag 0x8020 points at a u32 sample rate. Layouts are from one DT168 (firmware 2.7.x); other makes may differ, so parsers return no reading, never a guess.

In the code: app/modules/dante/arc.py (parse_counts, parse_info, parse_sample_rate, read_info), heartbeat.py (seen).
