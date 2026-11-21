---
title: "A fresh install asks for its first audio input and output, and an upgrade keeps its old input as lane 1"
author: Justin
date: 2026-11-21
branch: audio-first-setup-599
cost: { minutes: 6, tokens: 66304 }
id: l-24ae6d6f
scope: repo
status: active
src: agent
touches:
  - app/audiobus.py
  - app/config.py
  - app/main.py
  - app/static/audiobus.js
  - app/templates/admin.html
  - config.example.json
  - tests/test_audiobus_api.py
  - tests/test_auth.py
---

## Decided
Justin, 2026-11-21, issue 599: no silent default lane or output. A fresh bus shows a First setup card and the operator picks the first input and output. An upgrade is told from a fresh install by the old input keys being present in the config, and its input becomes lane 1.
- Setup patches alert tones and the test tone into every channel of the new output. Spoken-language destinations are left for the operator, since all languages on the same channels would play over each other.

## Tried and ruled out
- Seeding a default lane and output on the system default devices without asking: Justin wanted the operator to choose.

## Watch out
- config.example.json must not carry audio_source, audio_device or input_channel: a fresh install copies it and would be migrated as if it were an upgrade.
- routing.feeds() does not show destination routes on the audio bus; check audiobus.destination_patches() instead.

In the code: audiobus.migrate_legacy_input, audiobus.first_setup, audiobus.patch_first_output, POST /api/admin/audiobus/setup, static/audiobus.js drawSetup.
