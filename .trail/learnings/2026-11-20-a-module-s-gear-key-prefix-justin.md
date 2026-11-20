---
title: "A module's gear key prefix must be plain letters, two desks at one address must not share address keys, and core calls rename and routes from worker threads"
author: Justin
date: 2026-11-20
branch: audio-mixers-150
id: l-91a9ee54
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/boards.py
  - app/main.py
  - app/modules/crew/__init__.py
  - app/static/board.js
  - app/static/layout-render.js
  - app/static/layout.css
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
Issue 150 (2026-11-20), found while building Audio Mixers:
- Gear keys must match `[a-z]{2,12}:<id>`. A prefix with an underscore (`audio_mixers:`) is dropped by gear's key cleaning, adopt raises, and no channels are ever recorded, with nothing logged above debug. The desks use `mixer:<desk id>`.
- Two devices of one module at the same address are two boxes: add the `ip:`/`host:` keys only when the address is unique among them, or gear.adopt merges them into one record (same lesson as Blackmagic's ATEM + HyperDeck).
- Core calls a module's `rename` (Network page) and routes `apply` through `asyncio.to_thread`, so a driver that writes on an asyncio StreamWriter or UDP transport must hop back to its event loop (`call_soon_threadsafe` + a Future).

## Tried and ruled out
- Writing to the desk socket straight from rename/apply: not thread-safe; works in a test by luck only.

## Watch out
- Allen & Heath MIDI desks do not echo what Relay sends and do not send renames: the mirror shows Relay's own mute/fader at once and names are polled every 30 s.
- The X32 answers a query (address with no arguments) but not its own set; send the set, then the query, so the mirror shows the desk's word.

In the code: app/modules/audio_mixers/service.py (report, _on_loop, apply_route), app/gear.py KEY_RE and _clean_keys, tests/test_audio_mixers.py ("desks are gear and channels").
