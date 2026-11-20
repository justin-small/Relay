---
title: "Several ProPresenter computers have one primary that drives the show, and lighting takes several programs of any kind"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 13, tokens: 219830 }
id: l-4cce5f73
scope: repo
status: active
src: agent
touches:
  - app/modules/propresenter/propresenter.py
  - app/modules/lighting/service.py
  - app/modules/propresenter/__init__.py
  - app/modules/propresenter/ppcontrol.py
---

## Decided
Justin, 2026-11-19, issues 483 and 484, one pull request. ProPresenter: Relay keeps a list of computers and one primary; lyrics, timers, tags, lobby and the Planning Center link follow the primary, and another computer is controlled by making it primary. Lighting: a list of programs, mixed kinds and several of the same kind. Old single settings migrate to the first entry.
## Tried and ruled out
Per-computer followers for every ProPresenter feature (offered, not chosen). Control of a non-primary computer without switching primary: the control code reads the follower's active presentation and playlist, so it would need its own state per computer.
## Watch out
Issue 483's title says Planning Center but its body says ProPresenter; Justin chose ProPresenter. OBS, Reaper and Blackmagic already took several machines. Lighting cues of the first program keep bare ids on pages; other programs' cues are `<program>:<cue>`.
In the code: app/modules/propresenter/propresenter.py (clean_computers, host/port mirror the primary), app/modules/lighting/service.py (resolve, passwords JSON).
