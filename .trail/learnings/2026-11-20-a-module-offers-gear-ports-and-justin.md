---
title: "A module offers gear, ports and channels through one gear.<id> list(), and core records the answer"
author: Justin
date: 2026-11-20
branch: main
cost: { minutes: 10, tokens: 205105 }
id: l-5988214c
scope: repo
status: active
src: agent
touches:
  - app/gearfeed.py
  - tests/test_gearfeed.py
  - tests/test_shared_concepts.py
  - docs/modules.md
---

## Decided
Issue 545 (2026-11-19), Justin's answers: pull, not push. A module provides `gear.<id>` with a `list()` returning devices, ports and channels; core (`app/gearfeed.py`) asks and records. A module calls `gearfeed.changed("<id>")` when its gear changes. `Module.network_map()` and the Planning Center `mixer_channels` look-up are removed; modules no longer call `ports.sync` or `channels.report`. One PR for every bullet.
`network_map_adds` stays on the Module so the Sources card can describe a module while it is off. `network_changed()` stays: every one reconnects a service, none only reports ports.
Core's own sources (audio bus, PortAudio, metering) keep calling `channels.report`; `tally.publish` and `register_source` stay because they carry live readings, not a channel list.

## Tried and ruled out
Modules pushing through core's sync calls: not one shape, so not chosen.

## Watch out
`list()` returns None (or omits a device) while the module cannot see its gear, so nothing turns missing. Tests with their own service instance must `modules.provide("gear.<id>", {"list": inst.gear_list}, owner="<id>")`; a test that enables a module without attach() gets nothing recorded.
In the code: app/gearfeed.py, tests/test_gearfeed.py, the `seam` rule in tests/test_shared_concepts.py, docs/modules.md "Gear, ports and channels".
