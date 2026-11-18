---
title: "The Network page is built as the closets mockup: one page with Racks, Map, Ports and notes, This computer, Find and add, and UniFi's views as tabs on it"
author: Justin
date: 2026-11-18
branch: network-closets-redesign
cost: { minutes: 18, tokens: 312559 }
id: l-475feb58
scope: repo
status: active
replaces: l-09860a15
src: agent
touches:
  - docs/mockups/network/index.html
  - docs/mockups/network/shared.js
  - docs/mockups/network/network.css
  - app/network_map.py
  - app/main.py
  - app/templates/admin.html
  - app/static/network_map.js
  - app/modules/__init__.py
---

## Decided
Justin, 2026-11-18: build mockup B (closets) and compare it to the mockup screen by screen.
- Network map and Network are one page, #network; old hashes (#network_map, #unifi) open the right tab.
- A name given on Ports and notes changes only what Relay shows (own_name keeps the device's). Names, notes (device id, or "<switch id>#<port>" for an empty port) and places are kept in config "network_map", by MAC.
- Modules add tabs to a core page with AdminPage(tab_of="network"); UniFi is now Internet, Wi-Fi and who's on, NDI and Dante, UniFi console.
- Needs a look (port warnings, PoE at 85% of budget, AV device not answering, source errors) sits above the racks; NDI/Dante "flows" are UniFi's NDI and Dante tab (talking to), since nothing reads Dante subscriptions.

## Tried and ruled out
- Notes saved on every keystroke: Ports and notes keeps the Save notes button like Blackmagic; a name saves on Enter, a drawer note when you leave the field.

## Watch out
- network.js and network_map.js load the page on the "relay:tab" event from showPage: on a reload the page is still hidden when the scripts start, so a hashchange-only start shows nothing.
- Switch drawings come from UniFi port tables; SFP ports are drawn as plain ports.

In the code: app/network_map.py (clean_stored, save_labels, save_places, issues, labelled), routes /api/admin/network/map/labels and /places in app/main.py, the page in app/templates/admin.html, app/static/network_map.js, admin.css (.nm*), admin.js where()/showPage, app/modules/__init__.py admin_tabs/TAB_PAGES, app/modules/unifi/templates/unifi_*.html.
