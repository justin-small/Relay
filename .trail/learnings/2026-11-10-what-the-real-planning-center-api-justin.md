---
title: What the real Planning Center API sends that the docs and our first fake got wrong
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 7, tokens: 140148 }
id: l-9e38cc30
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - app/config.py
  - app/main.py
  - app/planningcenter.py
  - app/static/admin.js
  - app/templates/admin.html
  - tests/test_planningcenter.py
  - tools/check_planningcenter.py
---

## Decided
- Checked the client against the official docs and a live account (read-only). The fake Planning Center in the tests now answers the way the real one does.
- Archived and deleted service types are left out of the list; the docs do not say what those dates mean, so this is our choice.
- Paging stops on a short page rather than trusting a "next" link.

## Watch out
- The rate period header arrives as "20 seconds", not "20"; count and limit are bare numbers. Parsing it as an integer silently loses the period.
- A bad token gets 401 with a plain-text body ("HTTP Basic: Access denied."), not JSON. A valid token whose owner lacks Services access gets 403.
- Live list responses carry links.next, meta.next.offset and meta.total_count; per_page tops out at 100.
- The docs pages we could read never mention a User-Agent requirement; sending one works, going without is untested.
- Service type names are not unique on a real account (two "Wednesday PM"), so a picker that shows names alone is ambiguous.
- This repo has no required checks: `gh pr merge --auto` merges at once, before CI finishes.
- The docs site is a single-page app: fetch tools get an empty page, a real browser is needed. Docs now live under api.planningcenteronline.com/docs/overview/.

In the code: app/planningcenter.py (_note_rate, service_types), tests/test_planningcenter.py (the fake), tools/check_planningcenter.py with a git-ignored planningcenter-test.env for a live read-only check.
