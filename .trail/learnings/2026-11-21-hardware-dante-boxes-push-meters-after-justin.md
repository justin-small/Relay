---
title: "Hardware Dante boxes push meters after a port 8800 request, proven on a real stage box"
author: Justin
date: 2026-11-21
branch: claude/nice-perlman-8871aa
cost: { minutes: 6, tokens: 77278 }
id: l-06bdbfc2
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/metering.py
---

## Decided
On 2026-11-21, from dante-ad over Tailscale (Dante network 192.168.253.x), a real DT168 stage box accepted the port 8800 metering request (id 0x1200, opcode 0x3010) built by metering.py and pushed meters to UDP 8751 about 20 times a second. Pushes come in two layouts: version 3 (stage box, u16 counts, no trailing zero) and version 2 (u8 counts, trailing zero). Level scale is "detailed": 1 to 253 is -(byte-1)/2 dBFS, 254 muted.

## Tried and ruled out
Port 8752 (multicast metering) was not probed; the controller-request path covers stage boxes.

## Watch out
The request needs the sender's own MAC and IP; a device stops pushing a few seconds after the last request, so Relay renews every 3 seconds and sends a stop (destination zeroed) on shutdown.

In the code: app/modules/dante/metering.py, tests/test_dante_metering.py.
