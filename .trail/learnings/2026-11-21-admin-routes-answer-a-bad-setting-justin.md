---
title: "Admin routes answer a bad setting with the error's fixed message through HTTPException, never str(e), or CodeQL flags information exposure"
author: Justin
date: 2026-11-21
branch: claude/quirky-chebyshev-f44abc
cost: { minutes: 6, tokens: 14284 }
id: l-d67b7e8a
scope: repo
status: active
src: agent
touches:
  - app/modules/wireless_mics/routes.py
  - app/modules/wireless_mics/service.py
  - app/modules/wireless_mics/__init__.py
  - app/modules/wireless_mics/shure.py
  - app/modules/wireless_mics/static/wireless_mics.css
  - app/modules/wireless_mics/static/wireless_mics.js
  - app/modules/wireless_mics/templates/wireless_mics.html
  - docs/modules.md
  - docs/wireless-mics.md
  - tests/test_auth.py
---

## Decided
A module's own validation error carries a fixed sentence in `.message`; the route raises `HTTPException(400, exc.message)`, as Crew does. The admin page reads `detail`, so the sentence still shows.

## Tried and ruled out
Returning `JSONResponse({"error": str(e)})`: CodeQL's "Information exposure through an exception" alert on the PR (#623), even though the text was a fixed sentence.

## Watch out
CodeQL tracks str(exception) into a response whatever the text is; run `tests/run_security.py` but remember CodeQL only runs on GitHub.

In the code:
- app/modules/wireless_mics/service.py (Invalid.message), app/modules/wireless_mics/routes.py
