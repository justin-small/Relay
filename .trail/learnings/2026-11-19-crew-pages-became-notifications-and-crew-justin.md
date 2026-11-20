---
title: "Crew pages became notifications and crew epoch times became ISO text, keeping the permission id and old link"
author: Justin
date: 2026-11-19
branch: one-device-per-phone
cost: { minutes: 7, tokens: 210039 }
id: l-5b82efb9
scope: repo
status: active
src: agent
touches:
  - tests/test_shared_concepts.py
  - app/db.py
---

## Decided
- Issue 529: a crew "page" is a crew notification (table, module file, admin text, API). The `crew.page` permission id and audit action keep their names, so saved role settings and the activity history read the same. The old `/crew/page/<token>` link still answers, because a push sent just before the update carries it.
- Crew epoch columns became `<name>_at text` (ISO UTC). `crew_pages` had both `sent_ts` and `created_at`; `created_at` is the send time now. JSON the pages read (`sent_ts`, `done_ts`, `arrived_ts`, `last_ts`) is still epoch seconds, converted with `db.epoch()` at the edge, so no script changed.
- Remote intercom sessions are `intercom_sessions` rows with `via = 'remote'`.
## Tried and ruled out
- Editing old migrations 0002-0008: saved migrations are never edited, so the new `time` rule in `tests/test_shared_concepts.py` baselines them.
## Watch out
- Migration 0014 drops the child tables before `crew_pages`, or the cascade and set-null would wipe receipts and call-time runs. `tests/test_cleanups_529.py` checks this with foreign keys on.
In the code: app/modules/crew/migrations/0014_iso_times_and_notifications.sql, app/modules/intercom_remote/migrations/0002_sessions_into_intercom.sql, db.iso / db.epoch in app/db.py.
