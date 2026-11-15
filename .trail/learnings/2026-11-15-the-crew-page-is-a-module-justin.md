---
title: "The crew page is a module whose paths answer only on the HTTPS port, and its manifest and service worker are public"
author: Justin
date: 2026-11-15
branch: alerts-replies-history-events-216-219
cost: { minutes: 4, tokens: 138227 }
id: l-8e5c5596
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - app/main.py
---

## Decided
Issue 226: the crew app is a `crew` module (`/crew`, permissions crew.use member, crew.view viewer, crew.manage admin). A generic `Module.panel_prefixes` makes its paths 404 on the viewer port, so the tunnel (viewer port only) never carries them. A generic `modules.page_gate` hook gives a module's page the sign-in form. The manifest, service worker and offline page are public because browsers fetch them without the cookie; the service worker is served with Service-Worker-Allowed /crew and scope /crew (the page is /crew, not /crew/).
## Tried and ruled out
- A `crew.edit` operator permission: nothing uses it yet; the issues that add editing declare it.
## Watch out
- The service worker caches only its own shell files, never a page or /crew/api answer: those are per person.
- Not tried on a real phone: install prompts need a certificate the phone trusts.
In the code: app/modules/crew/, app/modules/__init__.py panel_prefixes, app/main.py _split_admin_port.
