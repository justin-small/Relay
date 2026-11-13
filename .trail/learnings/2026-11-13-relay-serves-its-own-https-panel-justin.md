---
title: Relay serves its own HTTPS panel on 443 and plain HTTP on 80; the viewer-port refusal follows the port actually bound
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 18, tokens: 215128 }
id: l-e20bd3c7
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/config.py
  - app/main.py
  - app/serving.py
  - app/static/admin.js
  - app/templates/admin.html
  - app/tls.py
  - run.py
  - tests/smoke_test.py
---

## Decided
Option A from issue 181: Relay terminates TLS itself, no proxy. One uvicorn server, one event loop; the panel socket speaks TLS, the viewer socket plain HTTP. Only the default ports 80/443 fall back (to 8080/8443); a port the operator chose that cannot be opened is an error. The panel and viewer ports can no longer be equal, because one shared port would serve the panel in cleartext. admin_host now defaults to 0.0.0.0 since the panel is HTTPS only; a stored 127.0.0.1 is kept as the operator's choice.

## Tried and ruled out
Wrapping a listening socket in ssl: asyncio cannot use it. Two uvicorn servers: two event loops, but the engine and hub are single-loop singletons. Making the viewer-port refusal depend on published ports only: dozens of tests use a test client on port 80, which collides with the new default viewer port, so the refusal uses the published port or else the configured one.

## Watch out
Test clients for admin routes must not use port 80 (the default viewer port); use relay.local:8001. Bind failures must show fixed sentences on the admin page, never the exception text, or CodeQL flags information exposure. On Windows use SO_EXCLUSIVEADDRUSE, or a taken port 80 is not detected and the fallback never runs. Windows and Linux first-run prompts are documented, not observed on a clean install. The tray fingerprint is not built (no tray yet), and docs/guide still describes the Docker and Caddy setup.

In the code: app/tls.py, app/serving.py (make_server adds the TLS socket after uvicorn starts the plain ones), run.py, app/main.py (_on_admin_port, _panel_headers, _serving_info), tests/test_serving.py.
