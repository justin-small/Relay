---
title: "UniFi module lands as four stacked PRs, reads a UniFi OS controller by API key, and finds NDI and Dante devices every way it can"
author: Justin
date: 2026-11-16
branch: unifi-module-358
id: l-09d03708
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-16, issue 358 (with #356):
- Four stacked PRs: (1) module foundation, controller connection and crew presence (#356); (2) network map; (3) NDI/Dante identification and traffic; (4) internet health. The issue's "More ideas" list is out of scope for now.
- Controller: UniFi OS (UDM/UCG/Cloud Gateway) with an X-API-KEY, read-only. The legacy stat/* paths are a fallback only where the integration API does not cover the need.
- NDI/Dante: use every source and cross-check: Relay's own mDNS (_ndi._tcp, _netaudio-*), well-known port numbers, and the private traffic-flows API when feature-detected. Per-port byte counters give traffic when flows are missing.
- Charts on the module's page plus a small WAN status tile on the main Relay page (through a module hook, never core importing the module). Relay pings the gateway and 1.1.1.1 itself for packet loss; the external target is a setting.
- Device registration: all three ways: "this is my phone" on the crew page (match the request IP to a client's MAC), an admin enters a MAC, and matching by the hostname the controller reports.
- No exclusions: guest networks and every VLAN count for presence and show on the map.
- History: 1-minute points for 48 hours, 5-minute points for 30 days, then deleted.

## Watch out
Phones use a private MAC per SSID; the crew page must tell people to turn it off for the church network, or hostname matching is the fallback.
