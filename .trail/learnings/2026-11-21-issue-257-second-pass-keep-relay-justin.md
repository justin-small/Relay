---
title: "Issue 257 second pass: keep Relay's own Dante discovery and add ConMon liveness, cheap ARC reads and SRV ports"
author: Justin
date: 2026-11-21
branch: tabs-and-signage-web-589-591
cost: { minutes: 8, tokens: 106037 }
id: l-65028021
scope: repo
status: active
src: agent
touches:
  - app/modules/dante/arc.py
  - tests/test_dante.py
---

## Decided
Justin's answers (2026-11-20): audit #257 against its "Done when" list and fill gaps, keeping the module's own mDNS and ARC code. Add three extras from the survey comments: ConMon multicast heartbeat as the liveness signal, the cheap ARC reads (0x1000 channel counts, 0x1002 device name, 0x1003 board/revision/hostname), and taking every port from SRV (Via uses 24440) with the inventory keyed by Dante name, not IP. Verify with fakes plus the full suite, and probe the real church network over Tailscale SSH to a Windows PC for the untested ARC-across-the-router question.

## Tried and ruled out
Switching to the netaudio package, or adding it alongside: not chosen; the earlier pass already ruled it out and the own-ARC code has since grown routing and heartbeat on top of it.

## Watch out
The ARC write and probe packets are still unproven on real gear until the SSH probe runs. Keep addresses, MACs and names out of the repo (it is public).

In the code: app/modules/dante/arc.py, heartbeat.py, service.py, monitor.py; tests/test_dante.py.
