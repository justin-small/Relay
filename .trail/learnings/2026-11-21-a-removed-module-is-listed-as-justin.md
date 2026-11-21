---
title: "A removed module is listed as retired so saved settings, switch and network routes for it are dropped on load"
author: Justin
date: 2026-11-21
branch: remove-aes67-603
cost: { minutes: 4, tokens: 84090 }
id: l-90d3978b
scope: repo
status: active
src: agent
touches:
  - app/audio.py
  - app/modules/__init__.py
---

## Decided
Issue 603: when a module is deleted, add its id to the retired list so a config saved with it is cleaned on load, instead of leaving dead settings and routes behind.

## Tried and ruled out
Relying on the module switch rebuild alone: it drops the switch but leaves the module's settings key and its network routes and policy in the saved config.

## Watch out
The Dante guide's new output section names the Audio bus page without the page having been opened in the app, and the docs example multicast address for the dante port (224.0.0.251:5353) is a guess; check both.

In the code: RETIRED_MODULES and Registry._drop_retired in app/modules/__init__.py; tested in tests/test_dante_inputs.py.
