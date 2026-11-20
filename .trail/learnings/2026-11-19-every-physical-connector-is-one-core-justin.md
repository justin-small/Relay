---
title: Every physical connector is one core port on its gear; traffic routes keep the adapter id beside the port ref so they work with the database down
author: Justin
date: 2026-11-19
branch: ports-540
cost: { minutes: 21, tokens: 349025 }
id: l-b36ae596
scope: repo
status: active
src: agent
touches:
  - app/migrations/0012_ports.sql
  - app/ports.py
  - app/modules/blackmagic/drawings.py
  - tests/test_ports.py
---

## Decided
Issue 540 (2026-11-19), Justin's answers: one PR for every bullet. Blackmagic's server holds the whole drawing (both faces, buttons and connectors), not just the connector list; bmdraw.js only draws the face it is sent. Traffic routes store the port ref, and the old known_ports list goes. Every physical port is a row: every switch port the map sees, every Blackmagic connector of the model, and this computer's hardware adapters. A virtual adapter (VPN, bridge) gets a row only once a route uses it, and it still shows on the Network page so it can be picked. A port the source stops reporting is marked missing, not dropped, so its note stays. Link "unknown" (null) stays apart from "no link".

## Tried and ruled out
Storing only the port ref in a route: Relay keeps running with its database down, and resolving would then need the database, so Dante (strict) would stop. Justin chose to store {"port": ref, "key": adapter id}. Resolving uses the key and never touches the database.

## Watch out
Blackmagic records connectors only while a device is connected. A disconnected Videohub reports no inputs, and syncing then would mark every port missing. Network and Blackmagic port notes now share one 300-character limit (Blackmagic's was 120). Tests that touch netroute need a test database (_db.start/_db.reset), or remember() would write to the real state database. Migrations run in netroute.startup(), after db.init, never in clean().

In the code: app/ports.py, app/migrations/0012_ports.sql, netroute.remember/migrate/_chosen, network_map.adopt_devices/stored_ports, blackmagic/drawings.py and service._faced, gear._merge moves ports. The new shared-concepts rule is "port".
