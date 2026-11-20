---
title: "Routes are core: a destination takes sources, the destination's module applies them, and the gear's word is shown beside what was asked"
author: Justin
date: 2026-11-20
branch: rename-543
cost: { minutes: 19, tokens: 546971 }
id: l-32996e50
scope: repo
status: active
src: agent
touches:
  - app/routing.py
  - app/audiobus.py
  - app/modules/ndi/router.py
  - app/modules/blackmagic/service.py
  - app/static/network_channels.js
---

## Decided
Issue 544 (2026-11-19), Justin's answers: one PR for every bullet. Gain is nullable (NULL when the gear has none). A route stores what was asked; the module that owns the destination applies it and reports what the gear really does, and a difference is shown, never overwritten. Saved NDI, Videohub and audio bus data moves to core by one migration on first read, and the old lists are emptied. The rundown needed no change: it already holds channel refs from #542.

## Tried and ruled out
- A separate gain table for audio routes only: offered, not chosen.
- Keeping no asked-for state and only reporting the gear: offered, not chosen.
- Copying data and keeping the old copy for a release: offered, not chosen.
- Splitting into three PRs: offered, not chosen.
- Providing the audio bus through modules.provide: it showed up in provided() and broke test_modules. Core providers use routing.provide_core instead.

## Watch out
- Config strips module keys not kept by the module's clean(), so legacy `routes`/`salvos` stay in NDI's defaults and clean_settings until migrated (marked allow-shared).
- config.save merges dicts; to drop keys pass replace=(KEY,).
- Recalling a Videohub preset now sends one command per output.

In the code: app/routing.py, app/migrations/0015_routes.sql, app/audiobus.py (matrix_from_routes), app/modules/ndi/router.py (migrate), app/modules/blackmagic/service.py (routes_api, _move_presets), tests/test_routing.py.
