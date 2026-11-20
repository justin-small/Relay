---
title: "An mDNS query socket binds to the one address that reaches the network and sets the multicast interface, never every interface"
author: Justin
date: 2026-11-16
branch: atem-discovery-real-hardware
cost: { minutes: 2, tokens: 16000 }
id: l-7f10de47
scope: repo
status: active
src: agent
touches:
  - spike/av_ports.py
---

## Decided
mDNS query sockets bind to a single local address (the one that routes toward the target network) and set IP_MULTICAST_IF to it. CodeQL flags binding to "" (py/bind-socket-all-network-interfaces) on every pull request.

## Tried and ruled out
Binding to "" on a Windows PC with several adapters (Tailscale, link-local ports): the multicast query left by another interface and got no answers at all, while the bound socket got every answer. So binding to one address is also what makes discovery work, not only what quiets CodeQL.

## Watch out
Devices on another VLAN (Dante stage boxes) are only visible while the UniFi gateway's mDNS proxy is on; with it off they disappear without any error. A discovery result that suddenly shrinks to the local VLAN means the proxy is off, not that the code broke.

In the code: spike/av_ports.py local_address(), mdns_multicast(), mdns_unicast(); --bind overrides the address.
