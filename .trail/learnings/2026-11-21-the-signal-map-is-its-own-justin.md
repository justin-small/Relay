---
title: "The signal map is its own module that reads Dante routing through a provided read-only hook, first as a pasted patch table"
author: Justin
date: 2026-11-21
branch: claude/eager-bun-ae6a03
cost: { minutes: 6, tokens: 115540 }
id: l-f2a85e83
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-21, issue 262: signal map is a separate module so it works with Dante off (pasted patch only). First slice is a pasted patch list (CH, NAME, PORT, SOCKET, UPSTREAM) stored as typed in its own table, matched to Dante transmit channels by name, shown as a table with mismatch states. Graph and A&H show-file import come later.
## Tried and ruled out
A tab inside the Dante module: needs Dante on, and collides with sessions editing dante.js/routes.py. An explicit channel@device CSV column: more work for the operator.
## Watch out
Dante exposes devices only through `dante.routing` (returns service.devices_copy); do not import dante from signalmap.
In the code: app/modules/signalmap/, tests/test_signalmap.py.
