---
title: "Live captions are a module that adds itself to the stream, and OBS and REAPER follow Start only when told to"
author: Justin
date: 2026-11-23
branch: issue-592-modules-follow-start
cost: { minutes: 15, tokens: 138012 }
id: l-bb6dc803
scope: repo
status: active
src: agent
touches:
  - app/config.py
  - app/engine.py
  - app/main.py
  - app/modules/propresenter/tagactions.py
  - app/scheduler.py
  - app/static/admin.js
  - app/stream.py
  - app/templates/admin.html
  - docs/guide/src/21-openai-key.md
  - docs/guide/src/33-admin-panel.md
---

## Decided
Justin, 2026-11-22, issue 592, after the first step shipped (#643): he wanted the rest done, not left for later. The caption pipeline (capture, transcription session, translator, recording hookup) is now the Captions module, app/modules/captions/pipeline.py. It adds itself to core's stream with stream.add_part and is on by default for every install. Core's engine keeps status, meter and the targets payload and answers with captions idle while the module is off. Backends, hub and viewer feeds stay core, as the Nov 20 learning decided. OBS (record and go-live per computer) and REAPER (record) follow Start and Stop only through their own opt-in checkboxes, and OBS stops only what Start began.
## Tried and ruled out
Stopping at the first step and calling the rest follow-ups: Justin asked why it was not done before the commit. Say up front when a job is bigger than one pass, and then do it rather than split it silently.
Rewriting the engine tests with swaps: tests attach the real captions module (modules.attach) or use a real stream part, which also lowered the honest-tests baseline.
## Watch out
In the code: tests that need the pipeline call modules.modules.attach("captions") and read engine.pipeline, or build Pipeline(Engine()) for a local one. engine.running, started_at, capture and last_error are read-only properties that read the pipeline. Permissions captions.view and captions.manage are pinned in tests/test_auth.py.
