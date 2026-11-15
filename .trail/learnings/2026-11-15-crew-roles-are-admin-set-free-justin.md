---
title: "Crew roles are admin-set free text in the crew module, and names are matched to Planning Center fuzzily with the rename left to an admin"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 1, tokens: 1558 }
id: l-bdaee82b
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/routes.py
---

## Decided
Justin answered questions on issue 227 (2026-11-15):
- The crew role is free text set only by an admin; crew see it read-only.
- A name matches a Planning Center person fuzzily (short forms like Mike/Michael, one-letter slips) when one person is clearly closest and no other account holds them.
- A confident match stores the Planning Center person id and only suggests Planning Center's spelling; an admin accepts or dismisses it. Relay never renames on its own.
- The role is not filled from Planning Center team positions.

## Tried and ruled out
- Crew editing their own role: offered, not chosen.
- Exact-name-only matching, and email matching: offered, fuzzy chosen.
- Auto-renaming the account to Planning Center's spelling, or linking with no rename suggestion: offered, not chosen.
- A role column on the core users table: the data lives in the crew module's own table instead.

## Watch out
Both surnames must be close and the first name close or a known short form, or there is no match; two equally close people mean no match. A link is never moved by a later scan; "Wrong person" removes it and remembers the refused name. Not seen in a browser.

In the code:
- `app/modules/crew/people.py` (`similarity`, `best_match`, `scan`; CONFIDENT 0.85, MARGIN 0.05)
- `app/modules/crew/migrations/0001_people.sql`
- `app/modules/crew/routes.py` (`/api/admin/crew/people`)
- `tests/test_crew_people.py`
