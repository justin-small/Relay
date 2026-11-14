---
title: "Per-user preferences are JSON values by user, scope and name, any signed-in user or token edits their own, and they stay out of the audit log"
author: Justin
date: 2026-11-14
branch: user-prefs-173
cost: { minutes: 1, tokens: 41342 }
id: l-f88b63f5
scope: repo
status: active
src: agent
touches:
  - tests/test_audit.py
  - tests/test_users.py
  - tests/test_auth.py
  - app/main.py
  - docs/modules.md
---

## Decided
Issue 173 (2026-11-14), defaults chosen without asking Justin:
- One table, user_prefs (user_id, scope, name) -> JSON text, cascade-deleted with the user. Scope "core" for core's, a module's id for a module's; modules call app/prefs.py directly (core never names a module).
- The HTTP API is under /api/admin/me/prefs so the viewer port and the tunnel never reach it (ADMIN_PREFIXES). Any signed-in role, Members included, and API tokens act on their own user's. A pending password change blocks it like other routes.
- The API accepts scope "core" or an installed module id (404 otherwise), so a browser cannot invent scopes.
- Limits instead of a schema: a value is at most 4000 bytes of JSON, 200 preferences per user across scopes.
- /api/admin/me carries "prefs" so a page has them at load without another request.
- No core UI uses a preference yet; the issue asked for the store.

## Tried and ruled out
- Recording preference changes in the audit log: a page saving its layout would bury it, and they change nothing for anyone else. main.AUDIT_SKIP lists the routes; tests/test_audit.py checks every core change route is in AUDIT_ROUTES or AUDIT_SKIP.

## Watch out
- Setting a pref for the setup token's stand-in Owner (id 0) is refused: there is no user row.
- tests/test_users.py counts core migrations (CORE_N) and tests/test_auth.py pins the /me keys; both change with any new migration or /me field.

In the code: app/prefs.py, app/migrations/0004_user_prefs.sql, app/main.py (AUDIT_SKIP, _pref_scope, admin_me_prefs, _me_payload), docs/modules.md "Per-user preferences", tests/test_prefs.py.
