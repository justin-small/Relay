---
title: "The network map is a core page every module feeds, not part of UniFi; the UniFi module lands as stacked PRs and finds NDI and Dante every way it can"
author: Justin
date: 2026-11-16
branch: unifi-module-358
id: l-d6e55e4f
scope: repo
status: active
replaces: l-09d03708
src: agent
touches:
  - docs/mockups/admin-shell/index.html
  - AGENTS.md
---

## Decided
Justin, 2026-11-16, issue 358 (with #356):
- The network map is shared by several modules, so it is a core page (Live group, network.view) that works whether UniFi is on or off. Modules add devices and facts to it through one core hook; core never imports a module. With no switch source, the map groups devices by the Relay network port they were found on, and the port table says a module that reads switches fills it in.
- The UniFi module keeps its own pages (Internet health, Settings) and is one source for the map.
- Stacked PRs: (1) UniFi foundation, controller connection and crew presence (#356); then the map, NDI/Dante and internet health. The "More ideas" list is out of scope.
- Controller: UniFi OS with a read-only X-API-KEY; legacy stat/* paths only as fallback.
- NDI/Dante: use every source (mDNS, port numbers, traffic flows when available); any source marking a device wins, and a disagreement is shown.
- Internet-health charts on the UniFi page plus a small tile on the main page; Relay pings the gateway and 1.1.1.1 (settable).
- Device registration for presence: "this is my phone" on the crew page, a MAC typed in by an admin, or the hostname.
- No exclusions: guest networks and every VLAN count.
- History: 1-minute points for 48 h, 5-minute points to 30 days.

## Watch out
Open: whether mDNS discovery of NDI and Dante is core or its own switchable module (AGENTS.md: integrations are modules). Mockup: docs/mockups/admin-shell/pages/core-network-map.js, spec docs/redesign/screens/core-network-map.md.
