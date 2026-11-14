---
title: "Crew pages are HTTPS with core roles, tenancy is one organisation per install, the tunnel carries only what a module needs, and /screen shows volunteer names"
author: Justin
date: 2026-11-14
branch: intercom-specs-237
cost: { minutes: 1, tokens: 2474 }
id: l-d085ab87
scope: repo
status: active
src: agent
touches:
  - app/users.py
  - docs/modules.md
  - app/main.py
---

## Decided
Justin, 2026-11-14, after a review of every open issue for conflicts:
- The crew page (#225, #226) and everything under it is served over HTTPS, not on the plain-HTTP viewer port. Phones only allow the microphone (intercom #240), service workers and web push in a secure context. The viewer port keeps only the public caption pages.
- Crew use the core roles (member, viewer, operator, admin, owner) and named permissions. The "admin, editor, volunteer" crew roles are dropped.
- Tenancy is one concept: one organisation per install, rows carry `org_id`. A multi-site church runs one install per campus, and Relay Cloud (#56) groups those installs under one organisation. "Campuses" and `company_id` are dropped; intercom tables use `org_id` and `intercom_orgs` (this overrides company_id in the intercom design learning).
- The remote access tunnel serves viewer routes plus only the routes a module needs in order to work, declared in code and tested (remote intercom in #277 and the crew page shell hosting its tab). Crew chat, check-in, checklists and files stay LAN-only.
- `/screen` showing teams, mics and photos (#148, #153) is by design: it is the production team's display. Audience caption pages and the panels' service stream never carry names.
- Integrations written into core files (#140 caption sink, #150 console mirror, #148 and #142 Planning Center paths) are modules; core gets hooks only.
- The fleet epic (#58, #51-#57) is a `fleet` module that needs `remote_access`, reuses #50's enrollment code and relay-cloud Worker, updates through the desktop updater, and puts the appliance on the headless Linux service.

## Watch out
- Phones must trust Relay's HTTPS certificate on a LAN address before the crew page works; the guide and the QR landing (#236) have to cover it.
- The #142 "song is live" signal is built before #143, #151 and #152.

In the code: app/users.py ROLES, docs/modules.md Permissions, app/main.py page_screen, app/modules/remote_access/.
