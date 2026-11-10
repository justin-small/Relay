---
title: "Planning Center token is saved whole but only 'connected' ever reaches the browser; a new token resets the followed service types"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 2, tokens: 96447 }
id: l-a19814f5
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - app/config.py
  - app/main.py
---

## Decided
- The application ID and secret are both treated as secret: the admin page gets only whether a token is saved and which service types are followed, never the ID or a hint of it.
- Saving a different ID or secret clears the followed service types, because they belong to the old account. Blank fields keep what is saved.
- The client waits out a 429 for Retry-After (clamped to 60 s) and gives up after 3 tries; it also pauses a window when the last reply said the limit was reached. The limit is read from the response headers, not hard-coded.

## Tried and ruled out
- Showing a hint of the ID like the OpenAI key's last four characters: the issue says show only that one is set.

## Watch out
- Error messages and logs must never carry the secret; the client strips it from anything it echoes, and tests/test_planningcenter.py checks log lines for it.
- Tests swap `planningcenter.BASE_URL` and `planningcenter._sleep`, so keep those module-level and read at call time.

In the code: app/planningcenter.py, config.redacted() in app/config.py, /api/admin/planning-center routes in app/main.py, tests/test_planningcenter.py.
