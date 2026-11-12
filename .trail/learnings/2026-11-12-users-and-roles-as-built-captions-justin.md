---
title: "Users and roles as built: captions outlive the database, and what was and was not checked"
author: Justin
date: 2026-11-12
branch: users-rbac
cost: { minutes: 33, tokens: 338548 }
id: l-6995815e
scope: repo
status: active
src: agent
touches:
  - app/main.py
---

## Decided
- A Postgres failure never stops captions: Caddy or the app exiting takes the container down, but Postgres is restarted on its own with backoff, and the admin page answers that the user database is unavailable meanwhile.
- Module routes each need a permission that starts with the module's own id; the module rule test fails a module admin route guarded by anything else.
- The lockout tool finds the container's database socket by itself, because a docker exec does not inherit what the entrypoint exported.
- A config save is refused whole if any one field in it is not allowed for that role, so pages send only the fields they own.

## Tried and ruled out
- Letting any supervised process exit stop the container (the first build): a database crash would have stopped a live service.
- Editing Windows batch files with a text-mode read and write: it silently turned CRLF into LF, which cmd misparses.

## Watch out
- On this Mac, initdb and pg_ctl need LC_ALL=en_US.UTF-8, and the socket directory must be a short path such as under /tmp (macOS limits socket paths to about 100 characters).
- Local tests ran on Homebrew Postgres 18; the image has Postgres 17. The full container was run once on macOS with setup, sign-in and the lockout tool; the Linux host-user path and Windows were never run.
- The guide's screenshots of sign-in, credentials and the sidebar are out of date.
- trail check reports seven conflicts on this branch; all are keyword matches against unrelated ProPresenter and Planning Center learnings.

In the code:
- docker/postgres.sh (pg_supervisor), tools/reset_owner.py, tests/_pg.py, app/main.py require().
