---
title: The crew page is core and modules add tabs to it; its service worker never stores the signed-in page
author: Justin
date: 2026-11-15
branch: crew-page-226
cost: { minutes: 6, tokens: 68004 }
id: l-3aecec6e
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/modules/__init__.py
  - app/static/crew-sw.js
  - app/static/crew.js
  - app/templates/crew.html
  - app/users.py
  - docs/modules.md
  - tests/test_auth.py
  - tests/test_crew.py
  - tests/test_users.py
---

## Decided
The crew page lives in core at /crew on the operator port only, so the viewer port and the remote access tunnel never reach it. Modules add tabs with a CrewTab (template, scripts, own permission); core's page never names a module's routes. The manifest and service worker need no sign-in because they hold nothing private; the page and its state need crew.use, a member-level permission. The worker is served from /crew/ with Service-Worker-Allowed: /crew so it controls the page at /crew itself, and manifest scope is /crew, not /crew/.

## Tried and ruled out
Caching the signed-in page for offline use: it belongs to one person and changes, so the worker keeps only static files and answers an unreachable page with a short offline page. Landing back on /crew after sign-in: sign-in lands on /home by an earlier decision, and /home links to /crew.

## Watch out
The page has not been installed on a real phone or seen in a browser. A secure context is needed for the worker, so the page shows a banner when it is not one. macOS sed -i needs an extension argument; use python for scripted edits.

In the code: app/main.py (crew_page, crew_manifest, crew_service_worker), app/static/crew.js, crew-sw.js, app/templates/crew.html, app/modules/__init__.py (CrewTab)
