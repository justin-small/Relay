---
title: "User accounts with roles: what Justin decided before any code was written"
author: Justin
date: 2026-11-12
branch: overlay-builder-139
cost: { minutes: 1, tokens: 1870 }
id: l-d9296880
scope: repo
status: active
src: agent
touches:
  - app/main.py
---

## Decided
- Roles are a fixed set (Owner, Admin, Operator, Viewer) built on named permissions, so custom roles can be added later.
- Permissions are per action (view versus change), not per page. The admin nav hides pages a user has no permission for, and the server checks every API route.
- Each module declares its own permissions; core never names an integration.
- Sign-in is username and password. A short PIN works only on devices an admin has marked trusted (a shared booth computer); everywhere else needs the password.
- Accounts are created by an admin. Relay has no email, so an admin resets forgotten passwords.
- Single sign-on comes later as a module; core only leaves a hook for a sign-in provider.
- The shared admin token creates the first Owner once, then is retired. A command-line reset covers lockouts.
- Display panels and other devices keep their existing authentication; devices are not users.
- One venue per install: roles apply to the whole install, no per-campus scope.
- Sessions last 30 days; disabling a user ends their sessions at once.
- Audit log, per-user preferences, per-user API tokens and non-admin logins are left out of the first piece of work. The schema leaves room; they are issues 172 to 175.

## Tried and ruled out
- Custom roles now: more UI and testing up front for no present need.
- Per-page access: too coarse to separate viewing from changing.
- PIN on any device: a short PIN alone is too easy to guess.
- Keeping the shared token as a permanent recovery sign-in.

## Watch out
- Justin wants the database unreachable from the LAN and asked whether to start on Postgres for future integrations. The advice given was SQLite behind one small core data layer with numbered migrations, so a later move is contained. He had not answered when this was written; ask before assuming.

In the code:
- admin_login in app/main.py is the shared-token sign-in this replaces.
