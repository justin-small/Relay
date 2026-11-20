---
title: "Dante monitoring raises problems as a status card and the stage alert named Dante, on request layouts that are unverified"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 1, tokens: 0 }
id: l-08bfa6f3
scope: repo
status: active
src: agent
---

## Decided
Issue 258, Justin's answers: use Relay's own Dante protocol code (not netaudio); alert on everything (device gone, subscription that does not resolve, clock unlocked or errors climbing, clock leader change too); each problem shows as a status card and health pill AND flashes the stage alert button named "Dante" when the operator has made one; test against a fake Dante device over real UDP on loopback. Live levels for this computer's own Dante channels plus channels the operator stars. A device counts as gone after two missed scans.

## Tried and ruled out
Asking netaudio via a fake executable: moot once Justin chose direct protocols.

## Watch out
No gear and no capture were available, so the clock (0x1200), subscription (0x3000) and level (0x3400) payload layouts in app/modules/dante/arc.py are Relay's assumption and the fake device in tests speaks the same assumption. Parsers return None on anything unreadable. Replace them after a real capture. Level channel names only apply when the level count equals the announced channel count.

In the code: `app/modules/dante/arc.py`, `app/modules/dante/monitor.py` (`Monitor.observe`), `app/modules/dante/migrations/0001_monitor.sql`, `tests/test_dante_monitor.py`.
EOF # trail-ok
