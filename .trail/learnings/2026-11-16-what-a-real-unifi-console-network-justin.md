---
title: "What a real UniFi console (Network 10.6) answers to a read-only API key, and what it does not"
author: Justin
date: 2026-11-16
branch: unifi-module-358
id: l-b5682613
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/__init__.py
  - app/modules/crew/routes.py
  - app/modules/crew/static/crew.js
  - app/modules/crew/static/crewadmin.js
  - app/modules/crew/templates/admin.html
  - app/modules/crew/templates/crew.html
  - docs/mockups/admin-shell/index.html
  - tests/test_auth.py
---

## Decided
Justin's UCG Max on Network 10.6.109, 2026-11-16, read with tools/check_unifi.py:
- A read-only API key reaches nearly everything: the integration API, v2 and the old `stat/*` paths, including the read POSTs (reports, traffic flows, the system log). Relay polls with the key alone.
- The client list comes from v2 `clients/active`. It is the only list with the name the UniFi app shows (`display_name`), plus `model_name`, `sw_port` and live byte rates. `stat/sta` has no names at all. The integration API is used last.
- The live event stream (`wss .../events`) needs the local user's session cookie. The API key is closed with 1000 at once. Relay uses the stream only to trigger a poll right away.

## Tried and ruled out
- `stat/event` (404) and `rest/alarm` (400) are gone. The system log replaces them: POST v2 `system-log/admin-activity` for the change log, `system-log/all` for client and ISP events.
- The console's own 5-minute WAN report covers only about 24 hours, so Relay keeps 30 days of history itself.

## Watch out
- admin-activity entries carry `message` as a template (`{ADMIN}`, `{PLATFORM}`, `{IP}`, `{SETTING_NAME}`, `{OBJECT}`, `{SECTION}`), filled from `admin.name`, `meta.actor`, `platform`, `ip` and `meta.display_property_*`. `message_raw` and `parameters` are empty there.
- PoE power, voltage and current arrive as strings ("4.20"). A radio's satisfaction of -1 means unknown.
- Relay's own local-user sign-ins show up in the console's admin-activity log.
- Flows include source and destination ports, so NDI and Dante can be spotted by port. Justin's network has no NDI or Dante traffic, so that classification is checked only against a fake.
- websockets 13's legacy client takes `extra_headers`, not `additional_headers`.

In the code: app/modules/unifi/controller.py (client_from_v2, READ_POSTS), readings.py (changes, _f), service.py (_events), tools/check_unifi.py, unifi-test.env.
