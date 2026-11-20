---
title: Dante receive-list reads work on a real stage box; writes are still unsent
author: Justin
date: 2026-11-20
branch: dante-arc-heartbeat-581
id: l-b707e064
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/arc.py
---

## Decided
On 2026-11-20 the read-only ARC requests (0x1000, 0x1002, 0x1003, 0x2000, 0x2010, 0x3000) were sent to a real DT168 stage box (StageBox-1-Top, 192.168.253.5, port 4440) from the basecenter server, and arc.parse_rx_page read its eight receive channels correctly, string pointers and all. Nothing was written to the device.

## Tried and ruled out
Sending from basecenter's Dante-network adapters (169.254.x): unreachable network. The stage box answers from the LAN adapter (10.10.0.35), because the router at 10.10.0.1 relays multicast DNS between the LAN and the Dante network and routes to 192.168.253.x.

## Watch out
Every receive channel had status word 1, which fits a source (Avantis-MainBoard) that is not on the network right now; 9 and 10 for a live subscription are still unseen. Subscribe and clear (0x3010, 0x3014) have not been sent to any real device. A page holds 8 to 16 records, so read pages until the result is not 0x8112.

In the code: app/modules/dante/arc.py (parse_rx_page, read_subscriptions).
