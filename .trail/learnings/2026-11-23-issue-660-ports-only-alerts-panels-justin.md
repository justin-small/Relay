---
title: "Issue 660 ports only alerts, panels, screens, boards and the signage modules to Rust; the calling modules wait for 661"
author: Justin
date: 2026-11-23
branch: rust-alerts-panels-660
cost: { minutes: 1, tokens: 2010 }
id: l-e1ac4dbc
scope: repo
status: active
src: agent
touches:
  - app/alerts.py
  - app/panels.py
  - app/screens.py
  - app/boards.py
---

## Decided
Justin, 2026-11-23, issue 660: port only what the issue names to Rust (alerts and stage messages, panels and check-in, screens, boards, and the signage modules signage and signage_web), as stacked PRs, core first. The Python modules that call alerts and panels (OBS, REAPER, Dante, NDI, ProPresenter, Planning Center, tones, alert_events) are not part of this issue; #661 ports them.

## Tried and ruled out
Porting every calling module in the same PR: about 27k lines and overlaps #658 and #661; Justin cut it back to the issue's own list.
Porting only the call sites: not possible, they sit inside modules that otherwise stay Python.

## Watch out
Until #661 lands, the Rust alerts and panels run only behind a switch, because a Python caller still talks to its own in-memory copy; do not turn both on together. The stress check for alerts was skipped at Justin's request, though the done-when line names it.
In the code: app/alerts.py, app/panels.py, app/screens.py, app/boards.py, app/modules/signage, app/modules/signage_web, native/.
