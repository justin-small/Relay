---
title: "Wireless mics slice 1 is Shure only, built from the spec and a fake receiver, and marked unproven"
author: Justin
date: 2026-11-21
branch: claude/quirky-chebyshev-f44abc
cost: { minutes: 7, tokens: 171456 }
id: l-aaca75d3
scope: repo
status: active
src: agent
touches:
  - app/modules/wireless_mics/__init__.py
  - app/modules/wireless_mics/routes.py
  - app/modules/wireless_mics/service.py
  - app/modules/wireless_mics/shure.py
  - app/modules/wireless_mics/static/wireless_mics.css
  - app/modules/wireless_mics/static/wireless_mics.js
  - app/modules/wireless_mics/templates/wireless_mics.html
  - docs/modules.md
  - docs/wireless-mics.md
  - tests/test_auth.py
---

## Decided
Justin, 2026-11-21 (issue 153): no Shure or Sennheiser receivers are on the LAN, so slice 1 is a Python `wireless_mics` module with Shure plus manual mics and packs, built against Shure's published command strings and a fake receiver, and labelled unproven on hardware everywhere (page, docs, summary), as the AES67 module was. Sennheiser EW-DX, Alerts, /screen cards and the gear list come later.

## Tried and ruled out
- Reusing the Blackmagic text client: modules cannot import each other, so a small client was written in the module.
- `SET 0 METER_RATE` (channel 0) as BabelBox does: Shure's bulletin does not show it for SET, so metering is turned on per channel.

## Watch out
- Sennheiser's adapter in BabelBox was proven on G3, not EW-DX.
- The scheduled person is found by position name from the followed plan, not by ref, because the rundown's people carry names only.

In the code:
- app/modules/wireless_mics/shure.py (client, unproven points listed in its docstring), docs/wireless-mics.md (what to check on a real receiver)
