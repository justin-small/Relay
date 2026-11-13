---
title: "Issue 138 checked live end to end: ProPresenter finds playlist items by exact name and needs both arrangement fields, never reports an API-triggered playlist a…"
author: Justin
date: 2026-11-13
branch: propresenter-138-verify
cost: { minutes: 21, tokens: 174743 }
id: l-95f1395e
scope: repo
status: active
src: agent
touches:
  - tests/test_pclink.py
  - tests/test_propresenter.py
  - tests/test_serving.py
  - app/tls.py
  - app/modules/propresenter/pclink.py
  - firmware/relay-display/main/timers_view.cpp
  - firmware/relay-display/main/discovery.cpp
---

## Decided
- Everything in issue 138 was run against the real ProPresenter 22, the real Planning Center account (a throwaway service type and plan, deleted after) and a flashed CrowPanel on 2026-11-13. Three things the fakes had wrong were fixed: the setlist swap's playlist write, Next/Previous item, and the timer digits' position on the panel.
- The setlist swap sends each presentation item under its exact ProPresenter name (kept from the library read, or read once from the presentation) and with empty arrangement_name and arrangement_uuid (the presentation's own arrangement).
- Next/Previous item steps through playlist/active when ProPresenter reports one, else the playlist Relay last triggered an item of, else playlist/focused.
- A new panel certificate also names what the one it replaces named (16 of each kind at most, current names first), so a host-name flip and back mints once, not twice.

## Tried and ruled out
- Matching playlist items by UUID: ProPresenter 22's PUT /v1/playlist/{id} ignores presentation_uuid and target_uuid and finds the item by id.name, exactly, trailing spaces included (404 otherwise). It rejects the whole body with 400 unless presentation_info has both arrangement fields.
- Relying on playlist/active after Relay triggers an item: it stays null for API triggers, and playlist/focused names no playlist. playlist/{id}/next/trigger works and counts a header as a step.

## Watch out
- Planning Center's go_to_next_item skips header items (checked live), so the drive loop's 12-press cap is not spent on headers.
- A playlist item with destination "announcements" goes to the announcements layer: that is how to make an announcement playlist through the API.
- The CrowPanel stays with the first Relay name it met. An old dns-sd -R announcer for the Docker Relay kept winning discovery; to bench a test Relay, run it with RELAY_INSTALL_NAME set to the panel's saved name.
- On macOS the host name comes from DHCP and falls back while Wi-Fi is down, which used to replace the panel certificate twice per drop.
- LVGL: lv_obj_get_y() on an object created this tick returns 0 until layout runs. Keep positions you set yourself rather than reading them back.

In the code:
- app/modules/propresenter/pclink.py (exact_names, _exact_name, build_playlist), ppcontrol.py (last_playlist, step_item)
- app/tls.py (_san, _keep, MAX_KEPT_NAMES), firmware/relay-display/main/timers_view.cpp (Row.digit_y)
- Tests: test_pclink (fake enforces the real PUT rules), test_propresenter (item stepping), test_serving (host-name flip)
