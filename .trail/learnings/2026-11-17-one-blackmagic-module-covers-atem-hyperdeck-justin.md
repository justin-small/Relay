---
title: "One Blackmagic module covers ATEM, HyperDeck and Videohub; the atem id was renamed with a one-time move of its settings, switch and network route"
author: Justin
date: 2026-11-17
branch: audio-metering-149
cost: { minutes: 13, tokens: 318221 }
id: l-30fb380b
scope: repo
status: active
src: agent
touches:
  - app/modules/blackmagic/textclient.py
  - tests/test_blackmagic_text.py
  - app/modules/__init__.py
  - tests/test_blackmagic.py
  - docs/modules.md
---

## Decided
- Justin, 2026-11-17, issues 432 and 433: the ATEM module became `blackmagic`, one module with a `kind` per device (atem, hyperdeck, videohub), one admin page, one traffic class, three permissions (blackmagic.view / control / manage). Presets are saved per Videohub in the device's own settings. Locking and unlocking a Videohub output is on the admin page behind blackmagic.manage (Justin chose it over leaving locks to the hardware panel).
- A renamed module sets Manifest.renamed_from; the registry moves the old settings key, on/off switch and network routes/policy once when config loads, and what is already under the new id wins. Permissions need no move because roles come from min_role.
- The tally source id stays `atem_<id>`, and a device saved before kinds is a switcher.
- Scope was the issues' suggested scope plus tests with fakes; triggering record or presets from the schedule or a ProPresenter slide tag was left out.

## Tried and ruled out
- Keeping the `atem` ids for compatibility: not chosen; Justin chose rename plus migration.

## Watch out
- HyperDeck (TCP 9993) and Videohub (TCP 9990) were written from Blackmagic's protocol documents and tested only against fakes. Nothing has run on real hardware yet. The ATEM 2 M/E Constellation on the church network announces `_videohub._tcp` on 9990 for its built-in router, so it is the one real Videohub-protocol device known to be there.
- Discovery keeps one entry per mDNS unique id and prefers the ATEM, so a Constellation's router is not listed separately; it has to be added as a Videohub by its address.
- Lock semantics ("O" held by this connection, released when it closes) are from the protocol document, not observed.

In the code: app/modules/blackmagic/ (service.py, hyperdeck.py, videohub.py, textclient.py, routes.py), app/modules/__init__.py (renamed_from, _move_renamed), tests/test_blackmagic.py and tests/test_blackmagic_text.py.
