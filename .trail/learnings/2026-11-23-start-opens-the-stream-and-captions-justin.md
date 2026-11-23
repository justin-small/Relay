---
title: Start opens the stream and captions are one optional part of it; the caption pipeline moving into a module is a later step
author: Justin
date: 2026-11-23
branch: issue-592-decoupling
cost: { minutes: 10, tokens: 150933 }
id: l-baa3c78d
scope: repo
status: active
src: agent
touches:
  - app/stream.py
  - tests/test_stream.py
  - app/engine.py
  - app/scheduler.py
  - tests/smoke_test.py
---

## Decided
Justin, 2026-11-22, issue 592 ("decoupling"): Start no longer means start captions. Core owns a stream (running flag and clock); captions run as one part while `stream_captions` is on (default on); every route to Start (button, schedule, ProPresenter capture-start tag) goes through it and it announces stream.started and stream.stopped for modules to follow. Breaking changes to the admin status were fine: `running` and `started_at` now mean the stream, `captions_running` is the captions. Existing modules should follow Start and Stop through those events, and Justin wanted the caption pipeline split out of the engine into a module with the backends staying in core.
## Tried and ruled out
Moving the whole pipeline into a module in the same change: engine, hub, viewers, panels, status feed and the scheduler all import the engine, and the Nov 20 learning keeps backends in core, so it needs its own design and pull request.
Auto-starting REAPER or OBS on Start: the Nov 17 REAPER learning ruled out "captioning running" as an auto-record trigger, so a module follows Start only when its own setting says so.
## Watch out
In the code: app/stream.py, `engine.status()` and `meter()` read the stream, the viewer feeds and status feed keep `running` for captions. main.py imports the stream as `show_stream` because `stream` is already a route function there. Tests swap `scheduler_mod.stream`, not the engine.
