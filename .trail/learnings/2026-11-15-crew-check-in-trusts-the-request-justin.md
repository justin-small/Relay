---
title: "Crew check-in trusts the request's LAN address, never stores a position, and clears arrivals when a new plan takes over"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 5, tokens: 56571 }
id: l-ea2d1b62
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/__init__.py
  - app/modules/crew/chat.py
  - app/modules/crew/migrations/0004_chat.sql
  - app/modules/crew/paging.py
  - app/modules/crew/routes.py
  - app/modules/crew/static/chat.js
  - app/modules/crew/static/crew.css
  - app/modules/crew/static/sw.js
  - app/modules/crew/templates/chat.html
  - tests/test_auth.py
---

## Decided
Justin chose these for issue 231:
- A phone is on the church network when its request reaches Relay's HTTPS port from a private LAN address. There is no outside lookup of a public IP. Loopback does not count, since that is a proxy or this machine.
- Arrival on the LAN is automatic when the crew page opens; a geofence needs a tap, because browsers only give location to an open page. The admin sets a centre and radius (default 150 m), Relay compares the distance on the host and keeps no coordinates.
- Operators (`crew.page`) see the board of Planning Center people on the plan against their team's earliest call time; crew see only their own status.
- Arrivals belong to the followed plan(s) and clear when a new plan takes over. With no plan the key is the date.
- UniFi device-join check-in was split out as issue 356.

## Tried and ruled out
- Comparing public IPs (needs an outside lookup and breaks behind carrier NAT or a proxy), and admin-typed subnets: offered, not chosen.
- Midnight reset: two services in a day would share arrivals.

## Watch out
Relay's Permissions-Policy blocked geolocation on every page, so a module now names the pages that may ask in `Module.geolocation_pages`, like `microphone_pages`. A phone whose location is rougher than 150 m does not count. Through a reverse proxy every peer would be loopback, so only the tap works. Not tried on a real phone.

In the code:
- `app/modules/crew/checkin.py` (`is_lan`, `inside`, `plan_key`, `board`)
- `app/modules/crew/migrations/0005_checkin.sql`
- `app/main.py` (`permissions_policy_for`) and `app/modules/__init__.py` (`geolocation_pages`)
- `tests/test_crew_checkin.py`
