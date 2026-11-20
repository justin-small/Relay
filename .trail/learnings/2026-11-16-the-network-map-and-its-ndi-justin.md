---
title: The network map and its NDI and Dante discovery are core; modules such as UniFi only feed the map
author: Justin
date: 2026-11-16
branch: unifi-module-358
id: l-8a4efca8
scope: repo
status: active
replaces: l-d6e55e4f
src: agent
touches:
  - docs/mockups/admin-shell/index.html
  - tests/test_modules.py
---

## Decided
Justin, 2026-11-16, issue 358: the network map is a core page (Live, network.view) that works with any modules on or off; modules add devices through one core hook and core imports none. mDNS discovery of NDI (_ndi._tcp) and Dante (_netaudio-*) is core too, not a switchable module: it only listens on Relay's own networks and calls no product's API. The UniFi module keeps Internet health and Settings and is one source for the map. Other #358 choices (UniFi OS API key, stacked PRs, every NDI/Dante source, no VLAN exclusions, 30-day downsampled history) stand.

## Watch out
If tests/test_modules.py flags the mDNS listener as core network code, add it to CORE_NETWORK and give this reason in the PR.

In the code: docs/mockups/admin-shell/pages/core-network-map.js, docs/redesign/screens/core-network-map.md
