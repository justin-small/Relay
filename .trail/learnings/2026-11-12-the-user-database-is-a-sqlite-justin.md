---
title: "The user database is a SQLite file beside the config, not Postgres"
author: Justin
date: 2026-11-12
branch: spike/freeze-179
cost: { minutes: 5, tokens: 186853 }
id: l-573ab6cf
scope: repo
status: active
replaces: l-2ff74757
src: agent
touches:
  - ".dockerignore"
  - ".github/workflows/tests.yml"
  - README.md
  - app/db.py
  - app/main.py
  - app/migrations/0001_users.sql
  - app/users.py
  - docker/Dockerfile
  - docker/docker-compose.yml
  - docker/entrypoint.sh
---

## Decided
Users and roles live in one SQLite file (relay.db beside config.json, RELAY_DATABASE overrides) so Relay can leave Docker and copying the state folder is a full backup. Postgres never shipped, so there was nothing to migrate. One connection and one lock give one writer; BEGIN IMMEDIATE replaces the advisory locks, which also covers a second process (the last-Owner race).
## Tried and ruled out
Bundling Postgres binaries for a desktop app (about 100 MB plus major-version upgrades). Plain `integer primary key` ids: autoincrement is used so ids are never reused.
## Watch out
Timestamps are UTC ISO text computed in Python (db.now / db.later), not SQL now(). Booleans are 0/1, so users._user wraps them in bool(). WAL on a Docker Desktop bind mount was not tried; db.py logs a warning if WAL is refused. PR #189 was admin-merged with required checks skipped, so CI (Linux) had not run when it merged.
In the code: app/db.py, app/users.py, app/migrations/0001_users.sql, tests/_db.py, tests/test_users.py.
