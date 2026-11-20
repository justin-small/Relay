---
title: "The person link is core's person_links, matching stays in Crew, and a refused spelling has its own table"
author: Justin
date: 2026-11-19
branch: shared-concepts-provide-using
cost: { minutes: 6, tokens: 205854 }
id: l-fcb62407
scope: repo
status: active
src: agent
---

## Decided
Issue 524 (2026-11-18): `person_links` (one row per user and provider, one user per provider id) and `app/directory.py` are core; Crew's fuzzy match (`people.scan`) and admin accept/dismiss/unlink write through `directory.link/clear_suggestion/unlink`. Planning Center provides `directory.pco` (people on the followed plans) and Crew's scan reads `directory.people("pco")`. With Crew off, existing links still answer `directory.user_for`; new matches need Crew on.

## Tried and ruled out
Keeping `dismissed_name` on the link row: unlinking deletes the row, so the refused spelling would be lost; it lives in `person_link_dismissed`.

## Watch out
The move is crew migration 0010 (copies from `crew_profiles`, then rebuilds the table, since a unique column cannot be dropped); 0001 keeps `pc_person text` marked `-- allow-shared` because applied migrations are history. `tests/test_directory.py` runs the migration from the old shape.
In the code: app/directory.py, app/migrations/0006_person_links.sql, app/modules/crew/migrations/0010_links_to_core.sql.
