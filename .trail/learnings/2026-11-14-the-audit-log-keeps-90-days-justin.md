---
title: "The audit log keeps 90 days, Admins and Owners read it, tokens cannot, and a middleware records every admin change"
author: Justin
date: 2026-11-14
branch: audit-log-172
cost: { minutes: 9, tokens: 227790 }
id: l-9c941ea9
scope: repo
status: active
src: agent
touches:
  - app/migrations/0003_audit_log.sql
  - app/audit.py
  - tests/test_audit.py
  - app/static/audit.js
  - docs/modules.md
  - app/main.py
  - app/users.py
---

## Decided
Justin chose (2026-11-14, issue 172): entries are kept 90 days (offered 1 year and an Owner setting); audit.view is Admin and up, and API tokens cannot read the log (it is in TOKEN_BARRED, like users.view).
- Every successful POST/PUT/PATCH/DELETE under /api/admin is written by one middleware after the response, so module routes are covered without core naming them. A route that says more calls audit.note(request, ...); otherwise the core route's line in main.AUDIT_ROUTES is used, and anything else (a module route) is "METHOD path".
- Sign-ins, failed sign-ins, the lockout (once, not each refusal during it), sign-out, first-run setup and import are recorded directly in the form routes.
- Entries copy the actor's name and username and have no foreign key, so they outlive a deleted user.
- MAX_ROWS (200k) caps the table whatever the age, so a flood of failed sign-ins cannot fill the disk.

## Tried and ruled out
- Writing what was typed as the username of a failed sign-in: it is sometimes a password typed in the wrong box. Only an existing account's username is written, otherwise "(unknown username)".

## Watch out
- audit.record never raises (a down database loses the entry); an action id must be dotted, e.g. "auth.signout", or it is dropped with an error log.
- tests/test_audit.py fails a new core admin route that changes something and is not in AUDIT_ROUTES.
- Settings changes record the field names, never values (the OpenAI key is one of them).

In the code: app/audit.py, app/migrations/0003_audit_log.sql, app/main.py (_audit_changes, AUDIT_ROUTES, _audit_failed_signin, /api/admin/audit), app/static/audit.js, docs/modules.md "The audit log".
