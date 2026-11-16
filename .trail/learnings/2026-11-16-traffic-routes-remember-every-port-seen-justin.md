---
title: "Traffic routes remember every port seen, core writes the module's health warning, and modules hear port changes through one hook"
author: Justin
date: 2026-11-16
branch: network-routing
cost: { minutes: 6, tokens: 81074 }
id: l-d2f8aabf
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/netinfo.py
  - tests/test_netinfo.py
  - app/netroute.py
  - tests/test_netroute.py
  - docs/modules.md
  - app/modules/__init__.py
---

## Decided
Issue 344, 2026-11-15, Justin's choices: routes live in config "network" with a known_ports list of every port Relay has seen, so a route to an unplugged USB adapter survives a restart (shown as missing); only a route to a never-seen id is dropped, with a notice. Core, not each module, turns a strict class's missing or down port into a "warn" health with the port's sentence first. Built stacked on the port-inventory branch (PR 405).
Media classes default to strict (not run, never moved to another network); others to fallback (default route, with a reason saying so). A policy equal to the class's default is not stored.
resolve() reads a cached port list fed by netinfo.watch and startup, never the interfaces itself, because health() and status polls call it.

## Watch out
network_changed is called on the event loop with every class of the module; a module that has just started is only recorded, not told (it resolved in start()).
route_problems() is the one place routes are refused; the Network page (#347) must call it before saving.

In the code: app/netroute.py, Manifest.traffic and Registry.health in app/modules/__init__.py, config "network" default, main._on_lan_change and _startup, tests/test_netroute.py, docs/modules.md "Network traffic on a port of its own".
