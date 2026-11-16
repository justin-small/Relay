---
title: "A slide tag fires a lighting cue by its name on a cue page, recorded as a system entry from the tag"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 5, tokens: 37980 }
id: l-df786469
scope: repo
status: active
src: agent
touches:
  - app/modules/lighting/__init__.py
  - app/modules/lighting/backend.py
  - app/modules/lighting/qlcplus.py
  - app/modules/lighting/routes.py
  - app/modules/lighting/service.py
  - app/modules/lighting/static/lighting.js
  - app/modules/lighting/templates/lighting.html
  - docs/modules.md
  - tests/test_auth.py
  - tests/test_lighting.py
---

## Decided
Issue 384, 2026-11-16, Justin's choices: the tag is [relay:light=Cue name], matched to a cue on a lighting page by name, any case; first page wins on a duplicate name. The audit entry is a system entry (no user) saying "(from a ProPresenter slide tag)". A refused or failed cue is a warning like other tags, never retried.

## Tried and ruled out
- Tag value as the program's cue id (s14, f12): exact but unreadable for slide authors.
- Name or id either way: not chosen.
- Presentation and slide in the audit detail: not chosen.
- Failing silently: not chosen.

## Watch out
- ProPresenter never imports lighting; it calls core's fire_cue(by_name=True, source=...). Name matching lives in lighting.
- Not run against a live QLC+ (it had stopped); tested with a stub hook and the fake backend.

In the code: app/modules/propresenter/tagactions.py (_tag_light), app/modules/__init__.py (fire_cue), app/modules/lighting/routes.py.
