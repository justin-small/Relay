---
title: "User accounts with roles: what Justin decided before any code was written"
author: Justin
date: 2026-11-12
branch: overlay-builder-139
cost: { minutes: 1, tokens: 12032 }
id: l-e3244851
scope: repo
status: active
replaces: l-d9296880
src: agent
touches:
  - app/main.py
  - docker/docker-compose.yml
  - docker/entrypoint.sh
---

## Decided
- The database is Postgres, chosen by Justin so later integrations that need a database share one. It must never be reachable from the LAN.
- Roles are a fixed set (Owner, Admin, Operator, Viewer) built on named permissions, so custom roles can be added later.
- Permissions are per action (view versus change). The nav hides pages a user cannot use, and the server checks every API route.
- Each module declares its own permissions; core never names an integration.
- Sign-in is username and password. A short PIN works only on devices an admin has marked trusted (a shared booth computer).
- An admin creates accounts and resets forgotten passwords, because Relay has no email.
- Single sign-on comes later as a module; core only leaves a hook.
- The shared admin token creates the first Owner once, then is retired. A command-line reset covers lockouts.
- Display panels keep their existing authentication; devices are not users.
- One venue per install. Sessions last 30 days; disabling a user ends them at once.
- Audit log, per-user preferences, per-user API tokens and non-admin logins are issues 172 to 175, left out of the first piece of work.

## Tried and ruled out
- SQLite: recommended for its zero upkeep on volunteer-run installs, but Justin preferred one real database for future integrations.
- Custom roles now, per-page access, a PIN on any device, and keeping the shared token as a permanent recovery sign-in.

## Watch out
- The relay container has a read-only root, drops all capabilities and runs as an unprivileged user with one bind-mounted state folder. Postgres needs its own writable data location and a start order the launch scripts handle.
- Copying the state folder no longer backs up everything once users live in Postgres.

In the code:
- admin_login in app/main.py is the shared-token sign-in this replaces.
- docker/docker-compose.yml and docker/entrypoint.sh define the container.
