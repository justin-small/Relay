---
title: "The signage link check only fetches public addresses, because CodeQL treats an admin-typed address as a server-side request forgery"
author: Justin
date: 2026-11-14
branch: signage-web-youtube-210
cost: { minutes: 2, tokens: 12346 }
id: l-9be85ce7
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/modules/signage_web/links.py
  - tests/test_signage_web.py
  - app/modules/signage/sources.py
  - app/modules/signage/static/player.js
  - app/modules/signage_web/__init__.py
  - app/modules/signage_web/routes.py
  - app/modules/signage_web/sourcing.py
  - app/modules/signage_web/static/signageweb.js
  - app/modules/signage_web/templates/admin.html
---

## Decided
- The check that asks a site whether it allows framing refuses any host that resolves to a private, loopback or link-local address, and checks each redirect hop the same way. Intranet addresses are saved unchecked and the page says so.

## Tried and ruled out
- Fetching whatever an admin typed, since admins are trusted: CodeQL failed the PR with a full server-side request forgery alert.
- A regex to tell "javascript:x" from "host:8080": CodeQL flagged it as slow on user input; plain string handling replaced it.

## Watch out
- CodeQL may still flag the fetch, because it does not recognise a host check as a sanitizer. Dismissing that alert is Justin's call.

In the code: app/modules/signage_web/links.py (public_host, _fetch, _clean_url), tests/test_signage_web.py.
