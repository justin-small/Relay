---
title: "Gear is one stored record found by MAC or address, a closet is a rack in a place of the same name, and Blackmagic keeps only its connection"
author: Justin
date: 2026-11-19
branch: main
cost: { minutes: 13, tokens: 218886 }
id: l-d2eb0ee5
scope: repo
status: active
src: agent
touches:
  - app/migrations/0011_gear_racks.sql
  - app/gear.py
  - tests/test_gear.py
---

## Decided
Issue 547 (2026-11-19), Justin's answers: one PR for every bullet; Blackmagic keeps only connection settings, and name, model, drawing, rack/shelf and order go onto gear; a network-map closet becomes a rack inside an auto-made core place of the same name; gear has a `position` for top-to-bottom order. Blackmagic "rack" means in a rack (default "Rack 1" in "Equipment room"), "shelf" means in that room with no rack. A record with a MAC is known by it alone; its ip/host keys are dropped so a new IP keeps the name. The name follows the source until an operator names it (`named`). Names, device notes and closets in config move onto gear on first read; port notes stay in config until #540.

## Tried and ruled out
Giving every Blackmagic device its address as a key: an ATEM and a HyperDeck at one address (a Constellation) merged into one record and fought over the name. A device now skips an address another Blackmagic device holds.

## Watch out
In the code: app/gear.py, app/migrations/0011_gear_racks.sql, network_map.adopt_devices/stored, blackmagic/service.py devices()/save_devices(). Two records join only when the map next reads a device carrying both keys. Tests that wrote Blackmagic names straight into config now go through save_devices.
