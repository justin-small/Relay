---
title: A new admin route fails CI until it is listed with its permission in the auth test
author: Justin
date: 2026-11-14
branch: signage-propresenter-208
cost: { minutes: 1, tokens: 1056 }
id: l-33efa063
scope: repo
status: active
src: agent
touches:
  - tests/test_auth.py
---

## Decided
Every route under /api/admin must have an entry in the auth test's permission table, because the test fails with "EVERY /api/admin route has a permission entry" otherwise.
## Watch out
The module contract test passes without the entry, so a new route can look finished locally and still fail CI on all three platforms. Run tests/test_auth.py, or the whole tests folder, before pushing. Issue 208's signage sync route hit this.
In the code: tests/test_auth.py, the list near the other ProPresenter routes (e.g. the link/refresh line).
