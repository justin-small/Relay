---
title: "Importing a v2.0 Docker install copies settings, never the certificate, and retires the shared admin token after the first Owner"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 3, tokens: 109431 }
id: l-bd45e2b6
scope: repo
status: active
src: agent
touches:
  - app/main.py
---

## Decided
- Import lives in core (app/importdocker.py), not a module: it copies Relay's own files, not another product. Reads the old folder only; second run is a no-op; a config that already holds its own key is not replaced without --force (kept as config.json.before-import).
- Container-only ports/hosts are dropped, /app/config paths made relative, PulseAudio device falls back to default with the existing notice. The old certificate is not imported; a new one is minted.
- The shared admin_token is kept in the imported config so first-run asks for it once, and is blanked right after the first Owner is created.
## Tried and ruled out
- Keeping tools/migrate-from-docker.ps1: it copied relay.db/accounts, which v2.0 never had.
## Watch out
- Not tried against a live Docker install or in the Tauri shell; the in-app offer is a path box on the first-run page (no folder picker yet).
In the code: app/importdocker.py, tools/import_docker.py, POST /admin/import in app/main.py, tests/test_importdocker.py.
