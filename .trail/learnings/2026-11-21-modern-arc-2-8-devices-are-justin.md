---
title: "Modern ARC (2.8+) devices are written with the 0x3410 page, proven on a real device, and the read-only guard is gone"
author: Justin
date: 2026-11-21
branch: claude/beautiful-lamarr-7af3fe
cost: { minutes: 12, tokens: 54240 }
id: l-0d3fe7ed
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/arc.py
  - app/modules/dante/routing.py
  - tests/test_dante_routing.py
---

## Decided
Justin, 2026-11-21, issue 584: devices announcing ARC 2.8 or newer get subscribe and clear as a 0x3410 page under protocol 0x2809, one record per page. Proven on a real device (Dante Virtual Soundcard on a Mac Studio, ARC 2.8.12): both accepted with result 1 and read back with the unchanged 0x3000 list, and the device was left as found. With that, the refuse-writes guard from #583 was removed. Unknown or unreadable versions still use the old 0x3010 packet.

## Tried and ruled out
Plain 0x3400 on 2.8 devices: refused (result 0x30), so it is not a simple listing. Via reports 0 receive channels, so it cannot be a write target. Protocols 0x280c and 0x280f were not tried.

## Watch out
The control port was reached from the Dante-network PC dante-ad over ssh (administrator, PowerShell only, no Python). Via listens on 24440, the rest on 4440; 4455 refuses everything.

In the code: arc.py (subscribe_page_request, is_modern, set_subscription modern=), routing.py (_write_one), tests/test_dante_routing.py (real bytes).
