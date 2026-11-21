---
title: "Dante writes are blocked only for ARC 2.8 and newer, as a removable guard until the modern format lands"
author: Justin
date: 2026-11-21
branch: claude/lucid-clarke-ca1c6b
cost: { minutes: 4, tokens: 59113 }
id: l-a7fdd5bf
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/routes.py
  - app/modules/dante/routing.py
  - app/modules/dante/static/dante.js
  - tests/test_dante_routing.py
---

## Decided
Issue 583 as written: refuse writes only to devices announcing ARC 2.8 or newer, and show them read-only. Kept small so the modern-format work (#584) can delete it whole.

## Tried and ruled out
Blocking every device (the owner's later comment on 583, after the Dante Controller capture): not done, because main's write packets already match the capture and it would disable routing entirely. Left as a one-constant change; the owner was asked.

## Watch out
Unknown or unparseable ARC versions are NOT blocked. The guard sends nothing, so it was not proven on real hardware.

In the code: ARC_MODERN, needs_modern_format in app/modules/dante/routing.py; read_only in routes.py and dante.js.
