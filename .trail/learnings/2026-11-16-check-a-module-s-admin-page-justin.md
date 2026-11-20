---
title: "Check a module's admin page in the real operator panel, and build it from the panel's own components"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 16, tokens: 249876 }
id: l-d346bbdf
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - app/modules/obs/service.py
  - app/modules/obs/routes.py
  - app/modules/obs/static/obs.js
---

## Decided
- 2026-11-16, after Justin found the first OBS admin page unformatted and unlike the rest of Relay: module admin pages are built from the operator panel's own pieces (main section with h3, .pcard with .phead/.pname/.pstate, dl/dt/dd stats, .pactions, .spill pills, .mhealth dots inside .pcard.mcard, .meterwrap/.metercol/.meter/.meterside/.meterread, details.sect for settings, .row/.field, label.check, .ctl, .note). Module CSS only lays those out, scoped under #page-<id>; links get color:var(--fg).
- Look at the page in the real panel before calling it done, at desktop width (1280) and phone width (375).

## Tried and ruled out
- A bare harness page with the module's script and no admin stylesheet: it hid that the page matched nothing.

## Watch out
- The panel is HTTPS with a self-signed certificate, which the in-app browser refuses. Run the app on the admin port without TLS on localhost instead (Chrome treats localhost as secure, so the sign-in cookie works): RELAY_CONFIG=<throwaway config with admin_port 18443> RELAY_DEMO=1 RELAY_ANNOUNCE=0 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18443, then create a throwaway Owner on /admin with the config's admin_token.
- A page that redraws its live part on a timer must not rebuild buttons each time, or a click that lands during a redraw is lost: rebuild only when what the buttons show changes.
