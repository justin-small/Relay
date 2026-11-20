---
title: "The lighting module ships as an interface with no backend; each program registers itself, and other modules fire cues through a core hook"
author: Justin
date: 2026-11-16
branch: network-guide
cost: { minutes: 5, tokens: 125941 }
id: l-4c62fa5b
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
---

## Decided
Issue 377, 2026-11-16, Justin's choices: the lighting module ships the Backend interface only, with no demo backend; ShowXpress, QLC+ and Vista each add theirs in their own issue. Pages and backend settings live in the module's own config, and view/control/manage are viewer/operator/admin. Other modules fire a cue through a core hook the module sets in attach(), not by importing it. Panel and screen press rules (per-panel keys, rate limits) are left to the surface issues.
Only a cue on a page can be fired through the hook; the admin routes may also fire any cue the program lists.
A press is sent once and never retried, because a dropped connection may already have done it.

## Tried and ruled out
- A built-in demo backend: not chosen, so tests use a fake registered in the test.
- Looking the module up by id in the registry to fire: not chosen, the core hook is the pattern.
- Rate limiting inside fire(): not chosen, it belongs with the panel and screen issues.

## Watch out
Nothing is run against a real lighting program, only a fake. The hook is set in attach(), not start(), so tests that never start the module still get it.

In the code: app/modules/lighting/ (backend.py BACKENDS and register), app.modules.fire_cue and cue_hook in app/modules/__init__.py, tests/test_lighting.py.
