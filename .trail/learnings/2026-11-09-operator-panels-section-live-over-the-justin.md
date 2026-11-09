---
title: "Operator Panels section: live over the status stream, and 'changed at the screen' is inferred from the revision"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 2, tokens: 82890 }
id: l-2226f538
scope: repo
status: active
src: agent
touches:
  - app/panels.py
  - app/main.py
  - app/static/admin.js
  - tests/test_panels.py
---

## Decided
- The Panels list rides the existing admin status stream as a `{"type":"panels"}` frame, pushed on every check-in, rename, feed set and forget. A panel going quiet sends nothing, so the stream's own 1 s tick compares which panels are online and sends a frame when that changes. No new timer or endpoint.
- "Feed last changed at the screen vs from admin" is inferred at check-in: the reported feeds changed, and if `feed_rev` rose it was the operator's feed applying, otherwise it was the screen. A first check-in after a Relay restart is not counted as a change.
- Languages are filtered per panel by its reported scripts; a panel that has not reported any is offered all of them (update() still refuses what it cannot draw).

## Watch out
- The admin page does not rebuild a card while its rename/feed form is open, or typing would be wiped by the next check-in frame; it rebuilds when the form closes.

In the code: app/panels.py (`online_signature`, `on_change`, `feeds_changed_by`), app/main.py (`_panels_frame`, admin_status_stream), app/static/admin.js (panels block), tests/test_panels.py.
