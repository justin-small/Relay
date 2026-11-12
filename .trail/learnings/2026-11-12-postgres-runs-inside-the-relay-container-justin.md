---
title: "Postgres runs inside the Relay container on a private socket, with dumps written beside the config"
author: Justin
date: 2026-11-12
branch: users-rbac
cost: { minutes: 5, tokens: 86792 }
id: l-2ff74757
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - README.md
  - app/config.py
  - app/main.py
  - app/overlays.py
  - app/static/admin.js
  - app/static/fonts/OFL-atkinson-hyperlegible.txt
  - app/static/fonts/OFL-source-serif-4.txt
  - app/static/fonts/VERSION
  - app/static/fonts/atkinson-hyperlegible-latin-700-normal.woff2
---

## Decided
- Justin chose to run Postgres as a third process inside the Relay container, beside Caddy and the app, so there is one container to run and the database has no network listener at all.
- Data lives on a Docker named volume, because Postgres is fragile on Docker Desktop bind mounts.
- Relay writes scheduled dumps into the state folder next to the config, so copying that one folder still backs up everything.
- Tests run against a real throwaway Postgres, so they test what ships.
- The shared admin token header stops working once the first Owner exists, the same as the sign-in form.

## Tried and ruled out
- A second Postgres container on a private Docker network: recommended for keeping the Relay container single-purpose, but Justin preferred one container.
- A data folder inside the state folder: bind-mount ownership trouble on macOS and Windows.
- SQLite in tests: two databases to keep compatible.

## Watch out
- The container cannot change file ownership and on Linux runs as the host user, who has no passwd entry; Postgres needs both solved before it will initialise.
- The Companion module specification signs in with the shared token header, so it has no way in until per-user API tokens (issue 174) exist.

In the code:
- docker/entrypoint.sh, docker/docker-compose.yml, COMPANION_MODULE.md.
