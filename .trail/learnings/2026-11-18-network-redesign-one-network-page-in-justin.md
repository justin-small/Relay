---
title: "Network redesign: one Network page in the closets layout, with names for Relay only, and UniFi adding its own tabs"
author: Justin
date: 2026-11-18
branch: blackmagic-rack-redesign
cost: { minutes: 1, tokens: 1974 }
id: l-09860a15
scope: repo
status: active
src: agent
touches:
  - app/templates/admin.html
  - app/static/network_map.js
  - app/network_map.py
  - app/modules/unifi/templates/unifi.html
---

## Decided
Justin, 2026-11-18: of three Network mockups (A tabs by view, B switches drawn in their closets like the Blackmagic rack, C health-first with NDI/Dante flows) he chose B. Tabs: Racks, Map, Ports and notes, This computer, Find and add.
- The network map and the ports-and-traffic page are one sidebar page, Network; ports and traffic is its This computer tab.
- Ports and notes lets the operator rename a device. The name changes only what Relay shows; nothing is sent to the device or UniFi. Names and notes are keyed by MAC.
- The UniFi module's views are tabs on the Network page when the module is on, added through a core hook for module tabs (core still imports no module).
- He wants all four new features: notes per port/device, places (closets) for switches with an order, a needs-a-look list (slow links, errors, PoE near budget, offline AV, traffic with no port), and NDI/Dante flows.

## Tried and ruled out
A (Map, Devices, Switches, This computer, Sources) and C (Overview, Video and audio, Map, Devices, Setup) as whole layouts: not chosen. C's needs-a-look list and flows were asked for as features inside B.

## Watch out
Dante flows (who subscribes to whom) are not read by anything in Relay; mDNS only says a Dante device exists. NDI flows depend on the NDI module knowing what each receiver watches.

In the code: mockups in docs/mockups/network/ (b-closet.html chosen; net.js holds the made-up data and the switch drawing, shared.js the This computer, Sources and device table panes). Serve from the repo root with the mockup-planning-center preview (port 8470). Pages: page-network_map and page-network in app/templates/admin.html, app/static/network_map.js, network.js, app/network_map.py; UniFi page app/modules/unifi/templates/unifi.html.
