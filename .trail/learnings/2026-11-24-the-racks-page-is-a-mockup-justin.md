---
title: "The Racks page is a mockup first, drawn from the rack-planner reference with sized devices, port info and cabling mode"
author: Justin
date: 2026-11-24
branch: racks-page-mockup
cost: { minutes: 8, tokens: 128861 }
id: l-54742791
scope: repo
status: active
src: agent
touches:
  - docs/mockups/racks/index.html
  - docs/mockups/racks/racks.css
  - app/gear.py
  - app/ports.py
  - app/cables.py
---

## Decided
Justin, 2026-11-24, issue 706: before building, a static mockup of a Racks page follows the reference screenshot: device library on the left, U-numbered rack with Front/Back, zoom and undo in the middle, device settings and wired ports on the right. Justin added per-device size in rack units (1U to 10U) and a port info card on click. Racks, gear, ports and cables stay the existing core tables; the page needs a U position and size on gear, a rack height and a way to add gear by hand.

## Tried and ruled out
Fixed-height page with the rack in its own scroll box: scrolling stopped at the bottom of the rack for a second before the page moved. The page now scrolls as one and the side panels are sticky with overscroll-behavior: contain.

## Watch out
Relay ports have no front or rear face, so Front/Back in the mockup only dims ports on the back view. Places are a flat list, so the reference's location tree is not drawn. Both are open questions on the issue.

In the code: docs/mockups/racks/index.html and racks.css; app/gear.py (racks, gear.position), app/ports.py, app/cables.py.
