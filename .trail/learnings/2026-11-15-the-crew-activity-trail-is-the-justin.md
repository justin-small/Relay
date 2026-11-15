---
title: "The crew activity trail is the audit log's crew entries, shown on a crew-page tab, and the QR points at a plain-HTTP join page"
author: Justin
date: 2026-11-15
branch: crew-activity-qr
cost: { minutes: 4, tokens: 99321 }
id: l-611b82c1
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/static/activity.js
  - app/modules/crew/routes.py
  - app/modules/crew/__init__.py
---

## Decided
Justin answered questions on issues 235 and 236 (2026-11-15):
- Activity trail: every crew action (chat, check-in, checklist, resource and page changes) goes in the existing audit log under `crew.*`, never with message text. It is read on an Activity tab of the crew page by anyone holding the new `crew.activity` permission (operator by default).
- QR code: shown on the admin Crew page and points at a plain-HTTP join page on its own module port (8095), which explains certificate trust, offers the public certificate, then links to the HTTPS crew page.
## Tried and ruled out
- Chat left out of the trail: offered, not chosen.
- A crew filter on the audit page only: offered, not chosen (editors could not see it).
- QR straight to the HTTPS address: offered, not chosen; a phone that distrusts the certificate gets only a browser warning, so no instructions could appear.
- Firmware panel QR: chosen but not built yet.
## Watch out
- `audit.note` is written only for `/api/admin` routes. Crew-page routes (`/crew/api/*`) must call `audit.record`; the old `note` calls there were silently lost.
- Existing installs need a restart to open the join port.
In the code: app/modules/crew/routes.py (`_log`, `activity`, join routes), app/modules/crew/__init__.py (Listener, `crew.activity`), tests/test_crew_activity.py.
