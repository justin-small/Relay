---
title: "Real Dante heartbeats read correctly, but only software devices send signal levels"
author: Justin
date: 2026-11-20
branch: dante-arc-heartbeat-581
id: l-2599fabf
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/heartbeat.py
  - tests/test_dante_heartbeat.py
---

## Decided
On 2026-11-20, from the basecenter server on the church Dante networks, 25 real heartbeat packets from 10 senders all read with heartbeat.py, and the 0x8001 clock record and 0x8002 signal presence record match byte for byte. Heartbeats arrive about once a second per device.

## Tried and ruled out
Using the heartbeat for stage-box levels: only 2 of 10 senders (a Dante Virtual Soundcard and one other PC) sent 0x8002; the four hardware boxes sent none, so their levels still need the metering requests on ports 8751 and 8752, which Relay does not make yet.

## Watch out
The church has two Dante networks on different subnets (172.31.x.x on NIC1, 192.168.253.x on NIC3); the same hardware box shows up on both with the same identity. Level 193 is about -96 dBFS and 255 means silence. Real record bytes are the fixtures in tests/test_dante_heartbeat.py, with the device identity replaced.

In the code: app/modules/dante/heartbeat.py, tests/test_dante_heartbeat.py.
